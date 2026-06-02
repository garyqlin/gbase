# SPDX-License-Identifier: MIT
"""
gbase/tools/mail.py

精灵邮箱 — AI-AI 链上通信。
基于 GBase 精灵邮箱系统（A2A/P2P）。
"""

import logging
import os

import httpx

from lib.toolkit import tool

logger = logging.getLogger(__name__)

MAILBOX_URL = os.getenv("OPPRIME_MAILBOX_URL", "")
CHAIN_URL = os.getenv("OPPRIME_CHAIN_ORANGE_URL", "")

# 当前 GBase 的邮箱地址
MY_ADDRESS = "zagu:)node3.gbase"  # 后续可根据节点 ID 动态获取


@tool()
async def check_inbox(to: str = "zagu") -> list:
    """查看精灵邮箱收件箱。默认查 zagu 的邮箱，其他可指定 to=xxx。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{MAILBOX_URL}/v3/mail/inbox", params={"to": to})
            result = resp.json()
            logger.info("收件箱: %s 条", len(result) if isinstance(result, list) else "?")
            return result
    except Exception as e:
        logger.error("查收件箱失败: %s", e)
        return {"error": str(e)[:200]}


@tool()
async def send_mail(to: str, subject: str, body: str) -> dict:
    """发送精灵邮箱邮件。

    to: 目标邮箱地址，格式 name:)nodeX.gbase
    subject: 邮件标题
    body: 邮件正文
    """
    payload = {
        "from": MY_ADDRESS,
        "to": to,
        "subject": subject,
        "body": body,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{MAILBOX_URL}/v3/mail/send",
                json=payload,
            )
            result = resp.json()
            logger.info("发信结果: %s", result)
            return result
    except Exception as e:
        logger.error("发送失败: %s", e)
        return {"success": False, "error": str(e)[:200]}


@tool()
async def list_all_mailboxes() -> list:
    """查看链上所有已注册的精灵邮箱地址。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{CHAIN_URL}/proof/list")
            proofs = resp.json()
            mailboxes = [
                p
                for p in (proofs if isinstance(proofs, list) else [])
                if isinstance(p, dict) and p.get("category") == "mailbox"
            ]
            return [{"hash": m.get("hash", ""), "description": m.get("description", "")} for m in mailboxes]
    except Exception as e:
        logger.error("查邮箱列表失败: %s", e)
        return {"error": str(e)[:200]}
