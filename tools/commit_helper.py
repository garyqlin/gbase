# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/commit_helper.py

AI commit message generator.
Auto-commit helper for agent-1 (engineering arm).
"""

import asyncio
import logging
import os
import sys

from lib.toolkit import tool

logger = logging.getLogger(__name__)
SKILL_DIR = os.path.expanduser("~/.qclaw/skills/YF-ai-commit-gen/scripts")


@tool()
async def suggest_commit_message(
    project_dir: str = "", commit_type: str = "", scope: str = "", message: str = ""
) -> dict:
    """根据当前 git diff 生成建议的 commit message。

    Args:
        project_dir: Project directory (default: current working directory)
        commit_type: 强制指定类型 feat/fix/docs/refactor/test/chore
        scope: 强制指定范围
        message: 自定义描述文本，不传则从 diff 自动推断

    Returns:
        建议的 commit message
    """
    workdir = project_dir or os.path.expanduser("~")

    cmd = [
        sys.executable or "python3",
        os.path.join(SKILL_DIR, "commit_gen.py"),
        "--short",
    ]
    if commit_type:
        cmd.extend(["--type", commit_type])
    if scope:
        cmd.extend(["--scope", scope])
    if message:
        cmd.extend(["--message", message])
    cmd.append("<<<")  # 用输入自动确认

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )

        # 输入 "n" 取消提交（只生成建议）
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=b"n\n"), timeout=15)

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        # 提取 commit message 部分
        msg = ""
        lines = stdout_text.split("\n")
        in_msg = False
        for line in lines:
            if "📝 建议" in line:
                in_msg = True
                continue
            if "====" in line and in_msg:
                break
            if in_msg and line.strip():
                msg += line + "\n"

        return {
            "success": proc.returncode == 0,
            "commit_message": msg.strip(),
            "raw_output": stdout_text[:2000],
            "errors": stderr_text[:500] if stderr_text else "",
        }
    except TimeoutError:
        return {"success": False, "error": "commit 建议超时（15秒）"}
    except Exception as e:
        return {"success": False, "error": str(e)}
