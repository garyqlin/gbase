# SPDX-License-Identifier: MIT
"""
gbase/tools/file_checker.py

File integrity checker tool.
"""

import asyncio
import logging

from lib.toolkit import tool

logger = logging.getLogger(__name__)

SKILL_DIR = "skills/YF-file-integrity"
SCRIPT = "scripts/check_integrity.py"


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
async def file_hash(path: str, algorithm: str = "sha256") -> dict:
    """Calculate file hash.

    Args:
        path: Target file path
        algorithm: Hash algorithm (md5/sha1/sha256/sha512), default sha256

    Returns:
        File hash value and metadata
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "hash", "--file", path, "--algorithm", algorithm]

    logger.info("Calculating file hash: %s", path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"error": f"File hash calculation failed: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "File hash calculation timed out"}
    except FileNotFoundError:
        return {"error": f"Skill script not found: {script}"}
    except Exception as e:
        logger.exception("file_hash exception")
        return {"error": str(e)}


@tool()
async def file_verify(path: str, hash_value: str, algorithm: str = "sha256") -> dict:
    """Verify if file hash matches.

    Args:
        path: Target file path
        hash_value: Expected hash value
        algorithm: Hash algorithm (md5/sha1/sha256/sha512), default sha256

    Returns:
        Verification result (whether it matches)
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "verify", "--file", path, "--hash", hash_value, "--algorithm", algorithm]

    logger.info("Verifying file hash: %s", path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"error": f"File verification failed: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "File verification timed out"}
    except FileNotFoundError:
        return {"error": f"Skill script not found: {script}"}
    except Exception as e:
        logger.exception("file_verify exception")
        return {"error": str(e)}
