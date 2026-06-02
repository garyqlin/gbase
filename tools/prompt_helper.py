# SPDX-License-Identifier: MIT
"""
gbase/tools/prompt_helper.py

Prompt optimization tool.
"""

import asyncio
import logging

from lib.toolkit import tool

logger = logging.getLogger(__name__)

SKILL_DIR = "skills/YF-prompt-optimizer"
SCRIPT = "scripts/optimize_prompt.py"


def _build_skill_path() -> str:
    import os

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, SKILL_DIR, SCRIPT)
    if os.path.exists(path):
        return path
    fallback = os.path.expanduser(f"~/.qclaw/skills/{SKILL_DIR}/{SCRIPT}")
    if os.path.exists(fallback):
        return fallback
    return os.path.join(base, SKILL_DIR, SCRIPT)


@tool()
async def optimize_prompt(prompt: str, action: str = "optimize") -> dict:
    """Optimize, compare, or manage prompt templates.

    Args:
        prompt: The prompt text to optimize (preserved for compare and version)
        action: Operation type — optimize, compare, version (list versions)

    Returns:
        Operation result
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", action]

    if action in ("optimize", "compare"):
        cmd.extend(["--prompt", prompt])

    logger.info("Executing prompt operation: %s --action %s", script, action)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"error": f"Operation failed: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "Prompt operation timed out"}
    except FileNotFoundError:
        return {"error": f"Skill script not found: {script}"}
    except Exception as e:
        logger.exception("optimize_prompt exception")
        return {"error": str(e)}
