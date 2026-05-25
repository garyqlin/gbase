# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/file_checker.py

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
    """计算文件哈希值。

    Args:
        path: 目标文件路径
        algorithm: 哈希算法（md5/sha1/sha256/sha512），默认 sha256

    Returns:
        文件哈希值和元信息
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "hash", "--file", path, "--algorithm", algorithm]

    logger.info("计算文件哈希: %s", path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"error": f"文件哈希计算失败: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "文件哈希计算超时"}
    except FileNotFoundError:
        return {"error": f"找不到 skill 脚本: {script}"}
    except Exception as e:
        logger.exception("file_hash 异常")
        return {"error": str(e)}


@tool()
async def file_verify(path: str, hash_value: str, algorithm: str = "sha256") -> dict:
    """验证文件哈希值是否匹配。

    Args:
        path: 目标文件路径
        hash_value: 期望的哈希值
        algorithm: 哈希算法（md5/sha1/sha256/sha512），默认 sha256

    Returns:
        验证结果（是否匹配）
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "verify", "--file", path, "--hash", hash_value, "--algorithm", algorithm]

    logger.info("文件哈希验证: %s", path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"error": f"文件验证失败: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "文件验证超时"}
    except FileNotFoundError:
        return {"error": f"找不到 skill 脚本: {script}"}
    except Exception as e:
        logger.exception("file_verify 异常")
        return {"error": str(e)}
