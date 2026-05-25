# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/jwt_helper.py

JWT encode/decode/verify tool.
"""

import asyncio
import logging

from lib.toolkit import tool

logger = logging.getLogger(__name__)

SKILL_DIR = "skills/YF-jwt-utils"
SCRIPT = "scripts/jwt_tool.py"


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
async def jwt_decode(token: str) -> dict:
    """Decode JWT token, parse header/payload.

    Args:
        token: JWT token string

    Returns:
        Decoded header and payload
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "decode", "--token", token]

    logger.info("Executing JWT decode")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return {"error": f"JWT decode failed: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "JWT decode timeout"}
    except FileNotFoundError:
        return {"error": f"Skill script not found: {script}"}
    except Exception as e:
        logger.exception("jwt_decode exception")
        return {"error": str(e)}


@tool()
async def jwt_verify(token: str, secret: str) -> dict:
    """Verify JWT token signature validity.

    Args:
        token: JWT token string
        secret: signing key

    Returns:
        Verification result (valid or not, payload info)
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "verify", "--token", token, "--secret", secret]

    logger.info("Executing JWT verify")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return {"error": f"JWT verify failed: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "JWT verify timeout"}
    except FileNotFoundError:
        return {"error": f"Skill script not found: {script}"}
    except Exception as e:
        logger.exception("jwt_verify exception")
        return {"error": str(e)}
