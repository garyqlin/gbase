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
    """验证 JSON/YAML 文件格式是否正确。

    Args:
        path: 目标文件路径

    Returns:
        验证结果（是否有效、错误详情）
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "validate", "--file", path]

    logger.info("验证文件格式: %s", path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return {"error": f"验证失败: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "文件验证超时"}
    except FileNotFoundError:
        return {"error": f"找不到 skill 脚本: {script}"}
    except Exception as e:
        logger.exception("validate_file 异常")
        return {"error": str(e)}


@tool()
async def infer_schema(path: str) -> dict:
    """从数据文件推断 JSON/YAML schema 结构。

    Args:
        path: 数据文件路径

    Returns:
        推断出的 schema 结构
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", "infer", "--file", path]

    logger.info("推断 schema: %s", path)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"error": f"schema 推断失败: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "schema 推断超时"}
    except FileNotFoundError:
        return {"error": f"找不到 skill 脚本: {script}"}
    except Exception as e:
        logger.exception("infer_schema 异常")
        return {"error": str(e)}
