# SPDX-License-Identifier: MIT
"""
village_connector.py — Village OS connector module

注入到 Opprime v2 启动流程中，完成：
1. 启动时注册到灵魂引擎（capability 声明）
2. 60 秒心跳
3. 提供 WCP 消息发送函数（经过 Security Gateway）
4. 从 Village OS 订阅消息
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
HEARTBEAT_INTERVAL = 60  # 秒
ENABLED = os.environ.get("VILLAGE_OS_DISABLED") != "1"


# ── 工具函数 ──


async def _http_post(path: str, data: dict) -> dict:
    """向 Village OS 发送 HTTP POST 请求。"""
    import aiohttp

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(f"{VILLAGE_OS_URL}{path}", json=data, timeout=aiohttp.ClientTimeout(total=5)) as resp,
        ):
            return await resp.json()
    except Exception as e:
        logger.warning("[Village] 请求失败: %s", e)
        return {"status": "error", "reason": str(e)}


async def _http_get(path: str) -> dict:
    """向 Village OS 发送 HTTP GET 请求。"""
    import aiohttp

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"{VILLAGE_OS_URL}{path}", timeout=aiohttp.ClientTimeout(total=5)) as resp,
        ):
            return await resp.json()
    except Exception as e:
        logger.warning("[Village] GET 请求失败: %s", e)
        return {"status": "error", "reason": str(e)}


# ── 核心 API ──


async def register() -> dict:
    """注册到 Village OS 灵魂引擎。"""
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
        logger.info("[Village] ✅ 已注册到灵魂引擎")
    else:
        logger.warning("[Village] ⚠ 注册失败: %s", result)
    return result


async def send_heartbeat() -> dict:
    """发送心跳到 Village OS。"""
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
    """经过 Village OS Security Gateway 发送 WCP 消息。

    示例：
        await send_message("mail", {
            "to": "yufei:)node1.opprime",
            "subject": "来自 Opprime",
            "body": "你好羽非"
        })
    """
    payload = {"type": msg_type, "from": VILLAGE_FROM, "to": to, "body": body}
    return await _http_post("/wcp/message", payload)


async def send_email(to: str, subject: str, body: str) -> dict:
    """通过 Village OS 发送精灵邮件（自动过安全网关）。"""
    return await send_message("mail", {"to": to, "subject": subject, "body": body, "action": "send"})


async def check_health() -> dict:
    """检查 Village OS 健康状态。"""
    return await _http_get("/health")


async def get_history(limit: int = 10) -> list:
    """获取 Village OS 消息历史。"""
    result = await _http_get(f"/wcp/history?limit={limit}")
    return result.get("messages", [])


async def get_soul_status() -> dict:
    """获取灵魂引擎状态。"""
    result = await _http_get("/wcp/status")
    return result.get("soul_stats", {})


# ── 启动循环 ──


async def start(_loop: asyncio.AbstractEventLoop = None) -> asyncio.Task:
    """在后台启动 Village OS 心跳和注册循环。

    用法：
        village_connector = await village.start()
        # 程序退出时：
        village_connector.cancel()
    """
    if not ENABLED:
        logger.info("[Village] Village OS 接入已禁用 (VILLAGE_OS_DISABLED=1)")
        return None

    async def _loop():
        # 首次注册
        await register()

        while True:
            with contextlib.suppress(Exception):
                await send_heartbeat()
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    task = asyncio.create_task(_loop())
    logger.info("[Village] 心跳循环已启动 (每 %ds)", HEARTBEAT_INTERVAL)
    return task
