# SPDX-License-Identifier: MIT
"""
search_tunnel.py — 搜索隧道桥（自爬版）

国内网络环境下自爬搜狗搜索引擎。
不再依赖第三方库或外部服务进程。
搜狗在国内稳定可用，无反爬门槛。
"""

import logging
import urllib.parse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SEARCH_TIMEOUT = 12
MAX_RESULTS = 8

# 搜狗反爬规避：使用桌面版 User-Agent
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


async def search_via_tunnel(query: str, count: int = 8) -> list[dict] | None:
    """自爬搜狗搜索，成功返回结果列表，失败返回 None。"""
    import asyncio

    try:
        url = f"https://www.sogou.com/web?query={urllib.parse.quote(query)}"
        loop = asyncio.get_event_loop()

        def _fetch():
            with httpx.Client(timeout=SEARCH_TIMEOUT, verify=False) as client:
                resp = client.get(url, headers=HEADERS, follow_redirects=True)
                resp.raise_for_status()
                return resp.text

        html = await loop.run_in_executor(None, _fetch)
        soup = BeautifulSoup(html, "html.parser")
        results = []

        # 搜狗结果解析：多个可能的容器类名
        for item in soup.select(".vrwrap, .rb, .vr-title, .vr_common, .result, .vr5k, .vrwrap, .res-list li"):
            title_el = item.select_one("h3 a, .vr-title a, a.vr-title, a[href^='http']")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if not href or href.startswith("#"):
                continue

            # snippet
            snip_el = item.select_one(".star-wiki, .str-text, .star-like, .str_info, .star-wiki, .space-txt")
            snippet = snip_el.get_text(strip=True)[:300] if snip_el else ""

            results.append(
                {
                    "title": title,
                    "url": href,
                    "snippet": snippet,
                    "source": "sogou",
                }
            )

            if len(results) >= count:
                break

        if not results:
            logger.warning("搜狗解析结果为空: %s", query)
            return None

        logger.info("搜狗搜索成功: %s -> %d 条", query, len(results))
        return results

    except httpx.TimeoutException:
        logger.warning("搜狗搜索超时: %s", query)
        return None
    except httpx.HTTPStatusError as e:
        logger.warning("搜狗搜索 HTTP %d: %s", e.response.status_code, query)
        return None
    except Exception as e:
        logger.warning("搜狗搜索异常 (%s)，回退自爬引擎", str(e)[:80])
        return None
