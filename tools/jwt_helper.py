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
    """解码 JWT token，解析 header/payload。

    Args:
        token: JWT 令牌字符串

    Returns:
        解码后的 header 和 payload
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "decode", "--token", token]

    logger.info("执行 JWT 解码")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return {"error": f"JWT 解码失败: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "JWT 解码超时"}
    except FileNotFoundError:
        return {"error": f"找不到 skill 脚本: {script}"}
    except Exception as e:
        logger.exception("jwt_decode 异常")
        return {"error": str(e)}


@tool()
async def jwt_verify(token: str, secret: str) -> dict:
    """验证 JWT token 的签名有效性。

    Args:
        token: JWT 令牌字符串
        secret: 签名密钥

    Returns:
        验证结果（是否有效、payload 信息）
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "verify", "--token", token, "--secret", secret]

    logger.info("执行 JWT 验证")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return {"error": f"JWT 验证失败: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "JWT 验证超时"}
    except FileNotFoundError:
        return {"error": f"找不到 skill 脚本: {script}"}
    except Exception as e:
        logger.exception("jwt_verify 异常")
        return {"error": str(e)}
