# SPDX-License-Identifier: MIT
"""
village_connector.py — Village OS connector module

Injected into Opprime v2 startup flow to:
1. Register with Soul Engine on startup (capability declaration)
2. 60-second heartbeat
3. Provide WCP message sending functions (via Security Gateway)
4. Subscribe to messages from Village OS
"""

import asyncio
import contextlib
import logging
import os

logger = logging.getLogger("village-os")

VILLAGE_OS_URL = os.environ.get("VILLAGE_OS_URL", "http://127.0.0.1:8765")
VILLAGE_NAME = "village:opprime:standard"
NODE_NAME = "opprime-v2"
VILLAGE_FROM = f"{VILLAGE_NAME}:{NODE_NAME}"
HEARTBEAT_INTERVAL = 60  # seconds
ENABLED = os.environ.get("VILLAGE_OS_DISABLED") != "1"


# ── Utilities ──


async def _http_post(path: str, data: dict) -> dict:
    """Send an HTTP POST request to Village OS."""
    import aiohttp

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(f"{VILLAGE_OS_URL}{path}", json=data, timeout=aiohttp.ClientTimeout(total=5)) as resp,
        ):
            return await resp.json()
    except Exception as e:
        logger.warning("[Village] Request failed: %s", e)
        return {"status": "error", "reason": str(e)}


async def _http_get(path: str) -> dict:
    """Send an HTTP GET request to Village OS."""
    import aiohttp

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"{VILLAGE_OS_URL}{path}", timeout=aiohttp.ClientTimeout(total=5)) as resp,
        ):
            return await resp.json()
    except Exception as e:
        logger.warning("[Village] GET request failed: %s", e)
        return {"status": "error", "reason": str(e)}


# ── Core API ──


async def register() -> dict:
    """Register with Village OS Soul Engine."""
    if not ENABLED:
        return {"status": "disabled"}

    payload = {
        "type": "capability",
        "from": VILLAGE_FROM,
        "body": {
            "name": "Opprime v2",
            "version": "2.0",
            "type": "agent",
            "identity": os.environ.get("IDENTITY", "standard"),
            "capabilities": [
                "chat",
                "learning",
                "search",
                "skills",
                "email_v3",  # removed feishu_messaging
            ],
            "endpoints": {
                # feishu: removed for release
            },
        },
    }
    result = await _http_post("/wcp/message", payload)
    if result.get("status") == "ok":
        logger.info("[Village] ✅ Registered with Soul Engine")
    else:
        logger.warning("[Village] ⚠ Registration failed: %s", result)
    return result


async def send_heartbeat() -> dict:
    """Send heartbeat to Village OS."""
    if not ENABLED:
        return {"status": "disabled"}

    payload = {
        "type": "heartbeat",
        "from": VILLAGE_FROM,
        "body": {
            "status": "ok",
            "uptime": __import__("time").time(),
            "mode": os.environ.get("IDENTITY", "standard"),
        },
    }
    return await _http_post("/wcp/message", payload)


async def send_message(msg_type: str, body: dict, to: str = "*") -> dict:
    """Send WCP message via Village OS Security Gateway.

    Example:
        await send_message("mail", {
            "to": "yufei:)node1.opprime",
            "subject": "From Opprime",
            "body": "Hello Yufei"
        })
    """
    payload = {"type": msg_type, "from": VILLAGE_FROM, "to": to, "body": body}
    return await _http_post("/wcp/message", payload)


async def send_email(to: str, subject: str, body: str) -> dict:
    """Send sprite mail via Village OS (auto-passes Security Gateway)."""
    return await send_message("mail", {"to": to, "subject": subject, "body": body, "action": "send"})


async def check_health() -> dict:
    """Check Village OS health status."""
    return await _http_get("/health")


async def get_history(limit: int = 10) -> list:
    """Get Village OS message history."""
    result = await _http_get(f"/wcp/history?limit={limit}")
    return result.get("messages", [])


async def get_soul_status() -> dict:
    """Get Soul Engine status."""
    result = await _http_get("/wcp/status")
    return result.get("soul_stats", {})


# ── Startup Loop ──


async def start(_loop: asyncio.AbstractEventLoop = None) -> asyncio.Task:
    """Start Village OS heartbeat and registration loop in the background.

    Usage:
        village_connector = await village.start()
        # On exit:
        village_connector.cancel()
    """
    if not ENABLED:
        logger.info("[Village] Village OS access disabled (VILLAGE_OS_DISABLED=1)")
        return None

    async def _loop():
        # Initial registration
        await register()

        while True:
            with contextlib.suppress(Exception):
                await send_heartbeat()
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    task = asyncio.create_task(_loop())
    logger.info("[Village] Heartbeat loop started (every %ds)", HEARTBEAT_INTERVAL)
    return task
