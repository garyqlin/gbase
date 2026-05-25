# SPDX-License-Identifier: MIT
"""
search_bridge.py — ProSearch agent

Runs locally, provides an HTTP search interface for agents.
Agents POST /search with query to get results.

Usage:
    python3 search_bridge.py [port]
    Default port: 8430

Auto-reports address to agents on startup.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys

try:
    from aiohttp import web
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "aiohttp", "--quiet"]
    )
    from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PROSEARCH = os.getenv("PROSEARCH_PATH") or ""  # Set PROSEARCH_PATH to enable search bridge
DEFAULT_PORT = 8430


async def handle_search(request: web.Request) -> web.Response:
    """Handle search requests. Receives POST /search {"query":"xxx", "count":5}

    Args:
        query: Search keyword (required)
        count: Number of results (optional, default 8)
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    query = body.get("query", "").strip()
    if not query:
        return web.json_response({"error": "Missing query parameter"}, status=400)

    count = body.get("count", 8)
    try:
        count = max(1, min(20, int(count)))
    except (ValueError, TypeError):
        count = 8

    logger.info("Search: query=%s count=%d", query, count)

    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            PROSEARCH,
            f"--keyword={query}",
            f"--cnt={count}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode("utf-8", errors="replace").strip()

        if not output:
            return web.json_response(
                {
                    "query": query,
                    "results": [],
                    "message": "Search returned no data",
                }
            )

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            logger.warning("ProSearch returned non-JSON: %s", output[:200])
            return web.json_response(
                {
                    "query": query,
                    "results": [],
                    "raw": output[:1000],
                }
            )

        if not data.get("success"):
            msg = data.get("message", "Search failed")
            logger.warning("Search failed: %s", msg)
            return web.json_response(
                {
                    "query": query,
                    "results": [],
                    "message": msg,
                }
            )

        # Parse ProSearch results
        inner = data.get("data", {}) or {}
        items = inner.get("docs", data.get("items", [])) or []
        results = []
        for item in items:
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("link", item.get("url", "")),
                    "snippet": item.get("snippet", ""),
                    "source": item.get("source", ""),
                    "time": item.get("time", ""),
                }
            )

        logger.info("Search results: %d items", len(results))
        return web.json_response(
            {
                "query": query,
                "results": results,
                "result_count": len(results),
            }
        )

    except TimeoutError:
        logger.warning("Search timed out: %s", query)
        return web.json_response(
            {
                "query": query,
                "results": [],
                "message": "Search timed out (15s)",
            }
        )
    except Exception as e:
        logger.error("Search error: %s", str(e))
        return web.json_response(
            {
                "query": query,
                "results": [],
                "message": f"Search error: {str(e)[:200]}",
            }
        )


async def handle_health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

    app = web.Application()
    app.router.add_post("/search", handle_search)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_health)

    logger.info("Search agent started on port %d", port)
    logger.info("API: POST http://localhost:%d/search", port)
    logger.info('     {"query": "search keyword", "count": 8}')

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    # Keep running
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
