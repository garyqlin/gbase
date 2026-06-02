# SPDX-License-Identifier: MIT
"""
tools/exec.py

命令执行工具。供 LLM 运行 Shell 命令（编译、测试、git 等）。
安全约束：
- 自动检测项目根目录（__file__ 所在项目的 parent）
- 默认 30 秒超时
- 只允许非交互式命令
"""

import asyncio
import os
from pathlib import Path

from lib.toolkit import tool

# 自动检测项目根目录（即为 tools/exec.py 所在项目的根）
# 允许的工作目录列表（战甲可以访问的项目目录）
_PROJECT_ROOTS = [
    Path(__file__).resolve().parent.parent,  # gbase
    Path("$GBASE_PROJECTS/nuoboke"),  # CRM 项目
    Path("$GBASE_PROJECTS"),  # 所有项目
    Path("$GBASE_STATE"),  # 工作区
]
_PROJECT_ROOT = _PROJECT_ROOTS[0]


@tool()
async def exec_command(command: str, timeout: int = 30, workdir: str = "") -> dict:
    """在 Shell 中执行命令（非交互式）。

    用于运行 Python 脚本、pytest 测试、git 命令等。
    只能在项目根目录及其子目录下执行。

    Args:
        command: 要执行的 Shell 命令（单行，非交互式）
        timeout: 超时秒数（默认 30，最大 120）
        workdir: 工作目录（留空默认项目根，也可传子目录名）

    Returns:
        执行结果：returncode / stdout / stderr / error
    """
    # 安全校验
    if not command or not command.strip():
        return {"error": "命令不能为空"}

    timeout = min(max(timeout, 1), 120)

    # 解析工作目录
    if workdir:
        # 绝对路径直接使用（多项目支持）
        target = Path(workdir) if workdir.startswith("/") else _PROJECT_ROOT / workdir
        # 防止 path traversal
        try:
            target = target.resolve()
            target.relative_to(_PROJECT_ROOT)
        except (ValueError, RuntimeError):
            return {"error": f"工作目录不在允许范围内: {workdir}"}
        workdir = str(target)
    else:
        workdir = str(_PROJECT_ROOT)

    # 创建目录（如果不存在）
    os.makedirs(workdir, exist_ok=True)

    try:
        import shlex

        cmd_parts = shlex.split(command)
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "error": f"命令执行超时（{timeout} 秒）",
                "command": command[:200],
                "workdir": workdir,
            }

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        result = {
            "returncode": proc.returncode,
            "stdout": stdout_text[:6000],
            "stderr": stderr_text[:2000],
            "workdir": workdir,
        }

        # 如果输出被截断，标记一下
        if len(stdout_text) > 6000:
            result["stdout_truncated"] = True
            result["stdout_full_length"] = len(stdout_text)
        if len(stderr_text) > 2000:
            result["stderr_truncated"] = True

        return result

    except Exception as e:
        return {"error": f"执行失败: {e}"}
