# SPDX-License-Identifier: MIT
"""
search_bridge.py — Multi-engine web search bridge (v2)

Replaces the old ProSearch proxy. Uses DuckDuckGo's search API via the `ddgs`
package — no API keys required, works in China with proper headers.

Usage:
    python3 search_bridge.py [port]
    Default port: 8430

API:
    POST /search  {"query":"...", "count":5}
    GET  /health
"""

import logging
import subprocess
import sys
import urllib.parse
import urllib.request
from typing import Any

try:
    from aiohttp import web
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "aiohttp", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "--quiet"]
    )
    from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_PORT = 8430


def _search_ddgs(query: str, count: int) -> list[dict[str, Any]]:
    """Search via DDGS (DuckDuckGo Search v2 / ddgs)."""
    try:
        from ddgs import DDGS
    except ImportError:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "ddgs",
                "-i",
                "https://pypi.tuna.tsinghua.edu.cn/simple",
                "--quiet",
            ]
        )
        from ddgs import DDGS

    s = DDGS()
    results = []
    try:
        for i, r in enumerate(s.text(query, max_results=count)):
            if i >= count:
                break
            results.append(
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", r.get("url", "")),
                    "snippet": r.get("body", r.get("snippet", "")),
                    "source": "duckduckgo",
                }
            )
    except Exception as e:
        logger.warning("DDGS search error: %s", e)
        # Try with a simple curl fallback
        try:
            html = _fetch_html(f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}")
            if html:
                results = _parse_html_results(html, count)
        except Exception as e2:
            logger.warning("DDG HTML fallback also failed: %s", e2)
    return results


def _fetch_html(url: str, timeout: int = 8) -> str | None:
    """Fetch HTML via urllib with a browser-like User-Agent."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _parse_html_results(html: str, count: int) -> list[dict]:
    """Parse DDG HTML search results page."""
    import re

    results = []
    # Match result blocks
    blocks = re.findall(r'<a[^>]*class="result__a"[^>]*href="[^"]*uddg=([^&"]+)[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
    for i, (url_enc, title_html) in enumerate(blocks):
        if len(results) >= count:
            break
        url = urllib.parse.unquote(url_enc) if url_enc else ""
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": "duckduckgo",
            }
        )
    return results


async def handle_search(request: web.Request) -> web.Response:
    """Handle search requests. POST /search {"query":"xxx", "count":5}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    query = body.get("query", "").strip()
    if not query:
        return web.json_response({"error": "Missing query parameter"}, status=400)

    count = max(1, min(20, int(body.get("count", 8))))

    logger.info("Search: query=%s count=%d", query, count)

    try:
        loop = asyncio.get_event_loop()
        # DDGS 在中国网络下引擎测试耗时长，设总超时 15 秒
        results = await asyncio.wait_for(
            loop.run_in_executor(None, _search_ddgs, query, count),
            timeout=15.0,
        )
        logger.info("Search results: %d entries", len(results))
        return web.json_response(
            {
                "query": query,
                "results": results,
                "result_count": len(results),
            }
        )

    except TimeoutError:
        logger.warning("Search timed out, trying HTML fallback: %s", query)
        results = []
        try:
            import subprocess as _sp

            out = _sp.run(
                [
                    "curl",
                    "-s",
                    "-m",
                    "6",
                    "-H",
                    "User-Agent: Mozilla/5.0",
                    "-H",
                    "Accept: text/html",
                    f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}",
                ],
                capture_output=True,
                text=True,
                timeout=8,
            ).stdout
            if out:
                results = _parse_html_results(out, count)
                logger.info("HTML fallback: %d results", len(results))
        except Exception as e2:
            logger.warning("HTML fallback also failed: %s", str(e2)[:60])
        if not results:
            return web.json_response(
                {
                    "query": query,
                    "results": [],
                    "message": "Search timed out",
                }
            )
        return web.json_response(
            {
                "query": query,
                "results": results,
                "result_count": len(results),
            }
        )
    except Exception as e:
        logger.error("Search exception: %s", str(e))
        return web.json_response(
            {
                "query": query,
                "results": [],
                "message": f"Search error: {str(e)[:200]}",
            }
        )


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def main():
    import asyncio

    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

    app = web.Application()
    app.router.add_post("/search", handle_search)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_health)

    logger.info("Search Bridge v2 started on port %d (DDGS backend)", port)
    logger.info('POST /search  {"query": "...", "count": 5}')
    logger.info("GET  /health")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    await asyncio.Event().wait()


import asyncio

if __name__ == "__main__":
    asyncio.run(main())
