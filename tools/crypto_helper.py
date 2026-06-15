# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/crypto_helper.py

密钥/证书/加解密工具 — 对接 YF-crypto-helper skill。
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
    """生成密钥对。

    Args:
        key_type: 密钥类型（rsa/ed25519），默认 rsa

    Returns:
        密钥生成结果（公钥和私钥路径/内容）
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "keygen", "--type", key_type]

    logger.info("生成密钥对: %s", key_type)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"error": f"密钥生成失败: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "密钥生成超时"}
    except FileNotFoundError:
        return {"error": f"找不到 skill 脚本: {script}"}
    except Exception as e:
        logger.exception("generate_key 异常")
        return {"error": str(e)}


@tool()
async def cert_info(path: str) -> dict:
    """查看 X.509 证书信息。

    Args:
        path: 证书文件路径（PEM 格式）

    Returns:
        证书详细信息（颁发者、有效期、主题等）
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "cert-info", "--file", path]

    logger.info("查看证书信息: %s", path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return {"error": f"查看证书失败: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "证书查询超时"}
    except FileNotFoundError:
        return {"error": f"找不到 skill 脚本: {script}"}
    except Exception as e:
        logger.exception("cert_info 异常")
        return {"error": str(e)}
