# SPDX-License-Identifier: MIT
"""
gbase/tools/chain.py

区块链存证工具 — 对接橘子链 (PoA 审计链)。
从 V0 chain.py 精简移植。
"""

import hashlib
import logging
import os

import httpx

from lib.toolkit import tool

logger = logging.getLogger(__name__)

ORANGE_URL = os.getenv("OPPRIME_CHAIN_ORANGE_URL", "")


@tool()
async def store_proof(data: str, category: str = "gbase_decision", description: str = "") -> dict:
    """将数据哈希锚定到橘子链进行存证（不可篡改）。"""
    data_hash = "sha256:" + hashlib.sha256(data.encode()).hexdigest()
    payload = {"hash": data_hash, "category": category}
    if description:
        payload["description"] = description[:200]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{ORANGE_URL}/proof/store", json=payload)
            result = resp.json()
            logger.info("存证结果: %s", result)
            return result
    except Exception as e:
        logger.error("存证失败: %s", e)
        return {"success": False, "error": str(e)[:200], "hash": data_hash}


@tool()
async def check_chain_health() -> dict:
    """检查橘子链是否在线。"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{ORANGE_URL}/health")
            return resp.json()
    except Exception as e:
        return {"status": "unreachable", "error": str(e)[:200]}
