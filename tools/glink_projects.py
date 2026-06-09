# SPDX-License-Identifier: MIT
"""
gbase/tools/glink_projects.py

Glink 项目记忆工具 — 让战甲通过 @tool 使用 Glink 的项目引擎。
战甲调扎古的 Glink daemon (8426)。
"""

import logging
import os
import re

import httpx

from lib.toolkit import register_toolset, tool

logger = logging.getLogger(__name__)

GLINK_BASE = os.environ.get("GLINK_BASE", "http://127.0.0.1:8426")
GLINK_TOKEN = os.environ.get("GLINK_API_TOKEN", "glink-secret-2026")


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if GLINK_TOKEN:
        h["Authorization"] = f"Bearer {GLINK_TOKEN}"
    return h


def _sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]


# ── 公共工具 ────────────────────────────────────────────


@tool()
async def tool_project_init(project_id: str, context: str = "") -> dict:
    """在 Glink 中创建或重建一个项目。所有项目的上下文、进度和事件都通过 Glink 统一管理。

    Args:
        project_id: 项目标识符（字母数字下划线，最长64字符）
        context: 可选的项目上下文 Markdown

    Returns:
        {"status": "ok", "project_id": "...", "path": "..."}
    """
    tid = _sanitize(project_id)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{GLINK_BASE}/project",
            json={"project_id": tid, "context": context},
            headers=_headers(),
        )
        return resp.json()


@tool()
async def tool_project_read_context(project_id: str) -> str:
    """读取 Glink 项目的 context.md 内容。

    Args:
        project_id: 项目标识符

    Returns:
        context 文本（如项目不存在返回 ''）
    """
    tid = _sanitize(project_id)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GLINK_BASE}/project/{tid}/context",
            headers=_headers(),
        )
        data = resp.json()
        return data.get("context", "")


@tool()
async def tool_project_update_context(
    project_id: str,
    context: str = "",
    event_type: str = "",
    event_detail: str = "",
) -> dict:
    """更新 Glink 项目的 context.md，并可选追加事件记录。

    Args:
        project_id: 项目标识符
        context: 新的完整 context Markdown（留空不更新 context）
        event_type: 事件类型，如 'step.completed'、'milestone.reached'、'decision.made'
        event_detail: 事件描述

    Returns:
        {"status": "ok", "project_id": "..."}
    """
    tid = _sanitize(project_id)
    async with httpx.AsyncClient(timeout=15) as client:
        if context:
            ctx_resp = await client.post(
                f"{GLINK_BASE}/project/{tid}/context",
                json={"context": context},
                headers=_headers(),
            )
            if ctx_resp.json().get("error"):
                return ctx_resp.json()

        if event_type:
            evt_resp = await client.post(
                f"{GLINK_BASE}/project/{tid}/event",
                json={
                    "type": event_type,
                    "agent": "zaku",
                    "detail": event_detail,
                },
                headers=_headers(),
            )
            if evt_resp.json().get("error"):
                return evt_resp.json()

        return {"status": "ok", "project_id": tid}


@tool()
async def tool_project_list() -> list:
    """列出 Glink 中所有注册的项目。

    Returns:
        项目列表
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{GLINK_BASE}/projects", headers=_headers())
        return resp.json().get("projects", [])


@tool()
async def tool_project_get(project_id: str) -> dict:
    """获取 Glink 项目的概览（进度、最后事件、context 摘要）。

    Args:
        project_id: 项目标识符

    Returns:
        项目详情字典
    """
    tid = _sanitize(project_id)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{GLINK_BASE}/project/{tid}", headers=_headers())
        return resp.json()


@tool()
async def tool_project_events(project_id: str) -> list:
    """读取 Glink 项目的事件流。

    Args:
        project_id: 项目标识符

    Returns:
        事件列表（按时间正序）
    """
    tid = _sanitize(project_id)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GLINK_BASE}/project/{tid}/events",
            headers=_headers(),
        )
        return resp.json().get("events", [])


@tool()
async def tool_project_archive(project_id: str) -> dict:
    """归档一个 Glink 项目（归档后不再活跃，但数据保留）。

    Args:
        project_id: 项目标识符

    Returns:
        {"status": "ok", "archived": true}
    """
    tid = _sanitize(project_id)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{GLINK_BASE}/project/{tid}/archive",
            headers=_headers(),
        )
        return resp.json()


# ── 注册到 toolset ────────────────────────────────────


def register():
    register_toolset(
        "glink_projects",
        [
            "项目", "项目上下文", "项目进度", "项目事件",
            "project", "context", "glink",
        ],
        [
            "tool_project_init",
            "tool_project_read_context",
            "tool_project_update_context",
            "tool_project_list",
            "tool_project_get",
            "tool_project_events",
            "tool_project_archive",
        ],
    )
