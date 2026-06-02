# SPDX-License-Identifier: MIT
"""
gbase/tools/cua_tools.py

CUA desktop operation tool.
"""

import asyncio
import logging
import os
import sys

from lib.toolkit import tool

logger = logging.getLogger(__name__)
SKILL_DIR = os.path.expanduser("~/.qclaw/skills/YF-cua-agent/scripts")


@tool()
async def cua_plan(action: str, target: str = "", url: str = "") -> dict:
    """Generate a CUA desktop action plan (planning only, no execution).

    Args:
        action: Action type: click|type|scroll|screenshot|navigate
        target: Description of the action target
        url: Navigation target URL (used with navigate action only)

    Returns:
        Action plan details
    """
    cmd = [sys.executable or "python3", os.path.join(SKILL_DIR, "cua_executor.py"), "--action", action]
    if target:
        cmd.extend(["--target", target])
    if url:
        cmd.extend(["--url", url])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=os.path.expanduser("~"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        return {
            "success": proc.returncode == 0,
            "plan": stdout.decode("utf-8", errors="replace")[:3000],
            "errors": stderr.decode("utf-8", errors="replace")[:500],
        }
    except TimeoutError:
        return {"error": "CUA plan generation timed out"}
    except Exception as e:
        return {"error": str(e)}


@tool()
async def cua_execute(action: str, target: str = "", url: str = "") -> dict:
    """Execute a CUA desktop action (plan output only, requires vision model for actual execution).

    Args:
        action: click|type|scroll|screenshot|navigate
        target: Action description
        url: URL (for navigate action)

    Returns:
        Execution result
    """
    return await cua_plan(action, target, url)


@tool()
async def memory_load(date: str = "") -> dict:
    """Load daily memory summary.

    Args:
        date: Date YYYY-MM-DD (leave empty for today)

    Returns:
        Memory summary content
    """
    cmd = [
        sys.executable or "python3",
        os.path.expanduser("~/.qclaw/skills/YF-daily-memory-loader/scripts/load_daily_memory.py"),
        "--action",
        "load",
    ]
    if date:
        cmd.extend(["--date", date])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        return {
            "success": proc.returncode == 0,
            "content": stdout.decode("utf-8", errors="replace")[:3000],
            "errors": stderr.decode("utf-8", errors="replace")[:500],
        }
    except Exception as e:
        return {"error": str(e)}


@tool()
async def memory_status() -> dict:
    """View memory file statistics."""
    cmd = [
        sys.executable or "python3",
        os.path.expanduser("~/.qclaw/skills/YF-daily-memory-loader/scripts/load_daily_memory.py"),
        "--action",
        "status",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return {"success": True, "status": stdout.decode("utf-8", errors="replace")[:2000]}
    except Exception as e:
        return {"error": str(e)}


@tool()
async def exec_safe(command: str) -> dict:
    """Safely execute a shell command, capturing output and return code.

    Args:
        command: Command to execute

    Returns:
        Execution result (stdout/stderr/return code/elapsed time)
    """
    cmd = [
        sys.executable or "python3",
        os.path.expanduser("~/.qclaw/skills/YF-exec-harness/scripts/exec_safe.py"),
        "--action",
        "run",
        "--cmd",
        command,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=35)
        return {
            "success": proc.returncode == 0,
            "output": stdout.decode("utf-8", errors="replace")[:3000],
            "errors": stderr.decode("utf-8", errors="replace")[:500],
            "returncode": proc.returncode,
        }
    except TimeoutError:
        return {"error": "Command timed out (30s)"}
    except Exception as e:
        return {"error": str(e)}


@tool()
async def manga_video_plan(files: str = "", script: str = "") -> dict:
    """Generate a manga-to-video production plan.

    Args:
        files: Image path (supports wildcards like "*.png")
        script: Narration text

    Returns:
        Video production plan
    """
    cmd = [
        sys.executable or "python3",
        os.path.expanduser("~/.qclaw/skills/YF-manga-video/scripts/manga_to_video.py"),
        "--action",
        "plan",
    ]
    if files:
        cmd.extend(["--files", files])
    if script:
        cmd.extend(["--script", script])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        return {
            "success": proc.returncode == 0,
            "plan": stdout.decode("utf-8", errors="replace")[:3000],
            "errors": stderr.decode("utf-8", errors="replace")[:500],
        }
    except Exception as e:
        return {"error": str(e)}


@tool()
async def manga_video_estimate(pages: int = 20, audio_min: int = 3) -> dict:
    """Estimate manga-to-video duration.

    Args:
        pages: Number of manga pages
        audio_min: Audio duration (minutes)

    Returns:
        Estimation result
    """
    cmd = [
        sys.executable or "python3",
        os.path.expanduser("~/.qclaw/skills/YF-manga-video/scripts/manga_to_video.py"),
        "--action",
        "estimate",
        "--pages",
        str(pages),
        "--audio_min",
        str(audio_min),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return {"success": True, "estimate": stdout.decode("utf-8", errors="replace")[:1000]}
    except Exception as e:
        return {"error": str(e)}
