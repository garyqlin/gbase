#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
anysearch_tool.py — AnySearch MCP 搜索工具包装

通过 mcporter CLI 调用 AnySearch MCP Server，失败时自动回退到本地 search_web。
"""

import json
import logging
import shlex
import subprocess

from lib.toolkit import tool

logger = logging.getLogger("anysearch")


def _call_mcporter(*args: str, timeout: int = 15) -> str:
    """执行 mcporter call，返回 stdout。不可用时抛错。"""
    cmd = ["mcporter", "call", "anysearch"] + list(args)
    logger.debug("mcporter: %s", shlex.join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"mcporter 失败 (rc={result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


@tool()
async def anysearch_search(
    query: str,
    max_results: int = 5,
    freshness: str = "",
    domain: str = "",
    sub_domain: str = "",
    zone: str = "",
) -> str:
    """AnySearch 通用搜索。通过 mcporter 调用 AnySearch MCP 引擎，失败时自动回退到 search_web。

    参数：
        query: 搜索关键词（必填）
        max_results: 返回结果数，默认 5，最大 20
        freshness: 时效筛选，可填 day/week/month/year，空表示不限
        domain: 垂直领域（如 finance/academic/code/health 等）
        sub_domain: 子领域（如 finance.us_stock 等）
        zone: 地理区域，cn 为国内，intl 为国际
    """
    try:
        parts = [f'query="{query}"', f"max_results:{max_results}"]
        if freshness:
            parts.append(f"freshness:{freshness}")
        if domain:
            parts.append(f'domain:"{domain}"')
        if sub_domain:
            parts.append(f'sub_domain:"{sub_domain}"')
        if zone:
            parts.append(f'zone:"{zone}"')
        return _call_mcporter("search", *parts, timeout=15)
    except (RuntimeError, FileNotFoundError) as e:
        logger.warning("anysearch mcporter 失败，回退到 search_web: %s", e)
        from tools.search import search_web

        return await search_web(query)


@tool()
async def anysearch_batch_search(queries_json: str = "", query: str = "") -> str:
    """AnySearch 批量搜索。失败时自动回退到单次 search_web。

    两个入参写法都支持：
    - queries_json: JSON 字符串，格式为 [{"query":"Q1"}, {"query":"Q2", "domain":"finance"}, ...]
    - query: 单一搜索词，自动转为单条批量查询
    """
    try:
        if query and not queries_json:
            queries_json = json.dumps([{"query": query}])
        return _call_mcporter("batch_search", f"queries:{queries_json}", timeout=30)
    except (RuntimeError, FileNotFoundError) as e:
        logger.warning("anysearch batch mcporter 失败，回退到 search_web: %s", e)
        q = query or json.loads(queries_json)[0]["query"]
        from tools.search import search_web

        return await search_web(q)


@tool()
async def anysearch_extract(url: str) -> str:
    """AnySearch 页面提取。抓取 URL 全文并转为 Markdown。

    参数：
        url: 要抓取的页面 URL（必须 http/https 开头）
    """
    try:
        return _call_mcporter("extract", f'url:"{url}"', timeout=30)
    except (RuntimeError, FileNotFoundError) as e:
        logger.warning("anysearch extract mcporter 失败，回退到 fetch_page: %s", e)
        from tools.search import fetch_page

        return await fetch_page(url)
