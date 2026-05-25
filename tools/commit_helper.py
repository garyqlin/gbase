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
    """Generate a suggested commit message from the current git diff.

    Args:
        project_dir: Project directory (default: current working directory)
        commit_type: Force specific type (feat/fix/docs/refactor/test/chore)
        scope: Force specific scope
        message: Custom description text. If not provided, auto-inferred from diff.

    Returns:
        Suggested commit message
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
    cmd.append("<<<")  # auto-confirm via stdin

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )

        # Input "n" to cancel commit (only generate suggestion)
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=b"n\n"), timeout=15)

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        # Extract commit message section
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
        return {"success": False, "error": "commit suggestion timed out (15 seconds)"}
    except Exception as e:
        return {"success": False, "error": str(e)}
