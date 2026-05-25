# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/schema_tools.py

JSON/YAML schema validation and inference tool.
"""

import asyncio
import logging

from lib.toolkit import tool

logger = logging.getLogger(__name__)

SKILL_DIR = "skills/YF-schema-validator"
SCRIPT = "scripts/validate_schema.py"


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
async def validate_file(path: str) -> dict:
    """Validate JSON/YAML file format.

    Args:
        path: Target file path

    Returns:
        Validation result (valid/invalid, error details)
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "validate", "--file", path]

    logger.info("Validating file format: %s", path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return {"error": f"Validation failed: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "File validation timed out"}
    except FileNotFoundError:
        return {"error": f"Skill script not found: {script}"}
    except Exception as e:
        logger.exception("validate_file exception")
        return {"error": str(e)}


@tool()
async def infer_schema(path: str) -> dict:
    """Infer JSON/YAML schema structure from a data file.

    Args:
        path: Data file path

    Returns:
        Inferred schema structure
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "infer", "--file", path]

    logger.info("Inferring schema: %s", path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"error": f"Schema inference failed: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "Schema inference timed out"}
    except FileNotFoundError:
        return {"error": f"Skill script not found: {script}"}
    except Exception as e:
        logger.exception("infer_schema exception")
        return {"error": str(e)}
