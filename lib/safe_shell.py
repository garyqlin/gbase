import shlex

# SPDX-License-Identifier: MIT
"""
lib/safe_shell.py

厚钢板层：统一 shell 命令执行底座。

所有 Gbase 实例调用子进程都走这里，不再手写 create_subprocess_exec。
特性：
- 支持 ~ 展开、管道、变量引用（走 /bin/zsh）
- 正确 asyncio.TimeoutError 捕获
- 超时自动 proc.kill() ，不留僵尸
- 统一输出格式
- 调用链可追踪
"""

import asyncio
import contextlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 允许的工作目录（每个实例可覆写）
_DEFAULT_ROOTS = [
    Path(os.environ.get("HOME", "/Users/gary")),
    Path("/tmp"),
]


# 高频 exec 并发上限
_SEMAPHORE = asyncio.Semaphore(100)


async def run(
    command: str,
    timeout: int = 30,
    workdir: str | None = None,
    cmdname: str = "sh",
    **kwargs,
) -> dict:
    """统一 shell 命令执行入口。

    Args:
        command: Shell 命令（支持 ~ 展开、管道、变量引用）
        timeout: 超时秒数（1-300）
        workdir: 工作目录（None 自动检测）
        cmdname: 命令名称（用于日志中区分调用来源）

    Returns:
        {"success": bool, "output": str, "error": str | None, "returncode": int}
    """
    timeout = min(max(timeout, 1), 300)
    _log = kwargs.pop("_log", logger)

    # 工作目录
    cwd = workdir or str(Path.cwd())

    async with _SEMAPHORE:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                executable="/bin/zsh",
            )
        except Exception as e:
            _log.error("[%s] 创建进程失败: %s", cmdname, e)
            return {"success": False, "output": "", "error": f"create process failed: {e}", "returncode": -1}

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)
            _log.warning("[%s] 命令超时 (%ds): %s", cmdname, timeout, command[:120])
            return {
                "success": False,
                "output": "",
                "error": f"命令执行超时（{timeout} 秒）",
                "returncode": 124,
                "_partial": True,
            }

        out_text = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace")

        return {
            "success": proc.returncode == 0,
            "output": out_text,
            "error": err_text or None,
            "returncode": proc.returncode,
        }


async def exec_command(
    command: str,
    timeout: int = 30,
    workdir: str | None = None,
    cmdname: str = "exec",
    **kwargs,
) -> dict:
    """兼容旧接口: tools/exec.py 风格的返回格式。

    返回 {success, stdout, stderr, returncode, error} 格式。
    """
    result = await run(command, timeout=timeout, workdir=workdir, cmdname=cmdname, **kwargs)
    return {
        "success": result["success"],
        "stdout": (result.get("output") or "")[:6000],
        "stderr": (result.get("error") or "")[:2000],
        "returncode": result["returncode"],
        "error": result.get("error"),
    }


# ── 兼容别名：旧版 tools/ 使用 run_exec(cmd: list[str]) ──
async def run_exec(
    cmd: list[str],
    timeout: float = 30,
    cwd: str | None = None,
    cmdname: str = "exec",
    timeout_msg: str = "Subprocess timed out",
) -> dict:
    """兼容旧接口：将 list[str] 转为 shell 字符串后调用 exec_command。"""

    command = " ".join(shlex.quote(arg) for arg in cmd)
    result = await exec_command(command, timeout=int(timeout), workdir=cwd, cmdname=cmdname)
    if result["returncode"] != 0 and not result["success"]:
        result.setdefault("error", timeout_msg)
    return result


# ── path_safety 兼容：从干将旧版 lib 迁移 ──
from pathlib import Path as _Path

_SAFE_BASE = _Path.home()  # 默认安全根目录，可通过环境变量覆盖


def validate_output_path(path: str, allowed_bases: list[str] | None = None) -> _Path:
    """验证输出路径在安全范围内，返回 resolved Path。

    Raises ValueError 如果路径逃逸到允许范围外。
    """
    p = _Path(path).expanduser().resolve()
    bases = [_Path(b).expanduser().resolve() for b in (allowed_bases or [_SAFE_BASE])]
    for base in bases:
        try:
            p.relative_to(base)
            return p
        except ValueError:
            continue
    raise ValueError(f"路径 {p} 不在允许范围内: {bases}")
