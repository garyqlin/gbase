# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/chain.py

Blockchain notary — PoA audit chain.
Ported from v0 chain.py.
"""

import hashlib
import logging
import os

import httpx

from lib.toolkit import tool

logger = logging.getLogger(__name__)

ORANGE_URL = os.getenv("OPPRIME_CHAIN_ORANGE_URL", "http://8.153.91.115:4200")


@tool()
async def store_proof(data: str, category: str = "opprime_decision", description: str = "") -> dict:
    """Anchor data hash to blockchain (tamper-proof)."""
    data_hash = "sha256:" + hashlib.sha256(data.encode()).hexdigest()
    payload = {"hash": data_hash, "category": category}
    if description:
        payload["description"] = description[:200]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{ORANGE_URL}/proof/store", json=payload)
            result = resp.json()
            logger.info("Proof stored: %s", result)
            return result
    except Exception as e:
        logger.error("Proof storage failed: %s", e)
        return {"success": False, "error": str(e)[:200], "hash": data_hash}


@tool()
async def check_chain_health() -> dict:
    """Check if blockchain is online."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{ORANGE_URL}/health")
            return resp.json()
    except Exception as e:
        return {"status": "unreachable", "error": str(e)[:200]}
