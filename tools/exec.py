# SPDX-License-Identifier: MIT
"""
tools/exec.py

命令执行工具。使用 lib/safe_shell 底座执行。
"""
import asyncio
import logging
import os
from pathlib import Path

import re

logger = logging.getLogger(__name__)

from lib.territory import check_territory_violation, build_territory_error
from lib.toolkit import tool
from lib.safe_shell import exec_command as _exec_command

_PROJECT_ROOTS = [
    Path(__file__).resolve().parent.parent,
    Path("/tmp"),
]
_PROJECT_ROOT = _PROJECT_ROOTS[0]


@tool()
async def exec_command(command: str, timeout: int = 600, workdir: str = "", **_kwargs) -> dict:
    """在 Shell 中执行命令（非交互式）。

    用于运行 Python 脚本、pytest 测试、git 命令等。
    只能在项目根目录及其子目录下执行。

    Args:
        command: 要执行的 Shell 命令（单行，非交互式）
        timeout: 超时秒数（默认 600，最大 600）
        workdir: 工作目录（留空默认项目根，也可传子目录名）

    Returns:
        执行结果：returncode / stdout / stderr / error
    """
    if not command or not command.strip():
        return {"error": "命令不能为空"}
    # LLM 传参可能把 int 参数传为字符串 -> 强制类型转换
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 600

    timeout = min(max(timeout, 1), 600)

    # ── 领地检查：命令中显式 cd 到其他 Agent 的家目录 ──
    # 扫描常见的路径操作模式（cd、>重定向、cp、mv、write to）
    cd_match = re.findall(r'(?:^|;|&&|\|\|)\s*cd\s+(\S+)', command)
    write_match = re.findall(r'((?:>|>>)\s*/[^\s;|&]+)', command)

    for target_path in cd_match + write_match:
        stripped = target_path.lstrip('> ').strip()
        violation = check_territory_violation(stripped)
        if violation:
            error_msg = (
                f"🚫 领地侵犯拒绝: 不能执行影响 Agent「{violation}」的命令。\n"
                f"这是其他 Agent 的领地。需要救援时，请用 rescue_tool 的 "
                f"check_brother / restart_brother / read_brother_log 工具。"
            )
            logger.warning(error_msg)
            return {
                "error": error_msg,
                "command": command,
                "violation": violation,
                "hint": "救援访问请使用 check_brother() / restart_brother() / read_brother_log()"
            }

    if workdir:
        target = Path(workdir) if workdir.startswith("/") else _PROJECT_ROOT / workdir
        try:
            target = target.resolve()
            target.relative_to(_PROJECT_ROOT)
        except (ValueError, RuntimeError):
            return {"error": f"工作目录不在允许范围内: {workdir}"}
        workdir = str(target)
    else:
        workdir = str(_PROJECT_ROOT)

    os.makedirs(workdir, exist_ok=True)

    try:
        result = await _exec_command(
            command=command,
            timeout=timeout,
            workdir=workdir,
            cmdname="exec_command",
        )
        return result
    except Exception as e:
        return {"error": f"执行失败: {e}"}
