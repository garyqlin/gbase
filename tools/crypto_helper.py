# SPDX-License-Identifier: MIT
"""
gbase/tools/crypto_helper.py

Crypto/encryption tool.
"""

import asyncio
import logging

from lib.toolkit import tool

logger = logging.getLogger(__name__)

SKILL_DIR = "skills/YF-crypto-helper"
SCRIPT = "scripts/crypto_tool.py"


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
async def generate_key(key_type: str = "rsa") -> dict:
    """Generate a key pair.

    Args:
        key_type: Key type (rsa/ed25519), default rsa

    Returns:
        Key generation result (public/private key paths or content)
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "keygen", "--type", key_type]

    logger.info("Generating key pair: %s", key_type)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"error": f"Key generation failed: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "Key generation timed out"}
    except FileNotFoundError:
        return {"error": f"Skill script not found: {script}"}
    except Exception as e:
        logger.exception("generate_key exception")
        return {"error": str(e)}


@tool()
async def cert_info(path: str) -> dict:
    """View X.509 certificate info.

    Args:
        path: Certificate file path (PEM format)

    Returns:
        Certificate details (issuer, validity, subject, etc.)
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "cert-info", "--file", path]

    logger.info("Viewing certificate info: %s", path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return {"error": f"Certificate query failed: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "Certificate query timed out"}
    except FileNotFoundError:
        return {"error": f"Skill script not found: {script}"}
    except Exception as e:
        logger.exception("cert_info exception")
        return {"error": str(e)}
