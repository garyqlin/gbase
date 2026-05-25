# SPDX-License-Identifier: MIT
"""
lib/exec.py

Command execution tool.
"""

import asyncio
import os
from pathlib import Path

from lib.toolkit import tool

# Auto-detect project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Allowed working directories
_PROJECT_ROOTS = [
    _PROJECT_ROOT,
]


@tool()
async def exec_command(command: str, timeout: int = 30, workdir: str = "") -> dict:
    """Execute shell command (non-interactive).

    For running Python scripts, pytest tests, git commands, etc.
    Only allowed within the project root and its subdirectories.

    Args:
        command: Shell command to execute (single line, non-interactive)
        timeout: Timeout in seconds (default 30, max 120)
        workdir: Working directory (empty for project root, or a subdirectory name)

    Returns:
        Execution result: returncode / stdout / stderr / error
    """
    # Safety check
    if not command or not command.strip():
        return {"error": "Command cannot be empty"}

    timeout = min(max(timeout, 1), 120)

    # Resolve working directory
    if workdir:
        # Use absolute paths directly (multi-project support)
        target = Path(workdir) if workdir.startswith("/") else _PROJECT_ROOT / workdir
        # Prevent path traversal
        try:
            target = target.resolve()
            target.relative_to(_PROJECT_ROOT)
        except (ValueError, RuntimeError):
            return {"error": f"Working directory not in allowed scope: {workdir}"}
        workdir = str(target)
    else:
        workdir = str(_PROJECT_ROOT)

    # Create directory (if not exists)
    os.makedirs(workdir, exist_ok=True)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
            shell=True,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "error": f"Command execution timed out ({timeout}s)",
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

        # Mark if output was truncated
        if len(stdout_text) > 6000:
            result["stdout_truncated"] = True
            result["stdout_full_length"] = len(stdout_text)
        if len(stderr_text) > 2000:
            result["stderr_truncated"] = True

        return result

    except Exception as e:
        return {"error": f"Execution failed: {e}"}
