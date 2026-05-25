# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/memory_profiler.py

Memory profiler tool.
"""

import asyncio
import logging

from lib.toolkit import tool

logger = logging.getLogger(__name__)

SKILL_DIR = "skills/YF-memory-profiler"
SCRIPT = "scripts/profile_memory.py"


def _build_skill_path() -> str:
    """Infer the skill script path based on the runtime environment."""
    import os

    # Prefer relative path (skills/ at the same level as tools/)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, SKILL_DIR, SCRIPT)
    if os.path.exists(path):
        return path
    # Fallback: ~/.qclaw/skills/
    fallback = os.path.expanduser(f"~/.qclaw/skills/{SKILL_DIR}/{SCRIPT}")
    if os.path.exists(fallback):
        return fallback
    # Last attempt: maybe the caller has cd to the right directory
    return os.path.join(base, SKILL_DIR, SCRIPT)


@tool()
async def analyze_memory(pid: int = 0, watch: bool = False) -> dict:
    """Analyze process memory usage and detect leak risks.

    Args:
        pid: Target process PID (0 means list all analyzable processes)
        watch: Whether to continuously sample and monitor (sample every 30s, 5 times total)

    Returns:
        Memory analysis result (JSON format)
    """
    script = _build_skill_path()
    cmd = ["python3", script]

    if pid > 0:
        cmd.extend(["--pid", str(pid)])
    if watch:
        cmd.append("--watch")

    logger.info("Running memory analysis: %s", " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            return {"error": f"Memory analysis failed: {stderr.decode().strip()}"}
        output = stdout.decode().strip()
        return {"result": output}
    except TimeoutError:
        return {"error": "Memory analysis timed out (>120s)"}
    except FileNotFoundError:
        return {"error": f"Skill script not found: {script}"}
    except Exception as e:
        logger.exception("analyze_memory exception")
        return {"error": str(e)}
