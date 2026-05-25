# SPDX-License-Identifier: MIT
"""
search_bridge.py — ProSearch agent

Runs locally, provides an HTTP search interface for agents.
Agents POST /search with query to get results.

用法:
    python3 search_bridge.py [端口号]
    默认端口 8430

Auto-reports address to agents on startup.
"""

import asyncio
import json
import logging
import subprocess
import sys

try:
    from aiohttp import web
except ImportError:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "aiohttp", "-i",
         "https://pypi.tuna.tsinghua.edu.cn/simple", "--quiet"]
    )
    from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PROSEARCH = "/Users/gary/Library/Application Support/QClaw/openclaw/config/skills/online-search/scripts/prosearch.cjs"
DEFAULT_PORT = 8430


async def handle_search(request: web.Request) -> web.Response:
    """处理搜索请求。收到 POST /search {"query":"xxx", "count":5}

    Args:
        query: 搜索关键词（必填）
        count: 返回结果数（可选，默认 8）
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "无效的 JSON"}, status=400)

    query = body.get("query", "").strip()
    if not query:
        return web.json_response({"error": "缺少 query 参数"}, status=400)

    count = body.get("count", 8)
    try:
        count = max(1, min(20, int(count)))
    except (ValueError, TypeError):
        count = 8

    logger.info("搜索: query=%s count=%d", query, count)

    try:
        proc = await asyncio.create_subprocess_exec(
            "node", PROSEARCH,
            f"--keyword={query}",
            f"--cnt={count}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode("utf-8", errors="replace").strip()

        if not output:
            return web.json_response({
                "query": query,
                "results": [],
                "message": "搜索无返回数据",
            })

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            logger.warning("prosearch 返回非 JSON: %s", output[:200])
            return web.json_response({
                "query": query,
                "results": [],
                "raw": output[:1000],
            })

        if not data.get("success"):
            msg = data.get("message", "搜索失败")
            logger.warning("搜索失败: %s", msg)
            return web.json_response({
                "query": query,
                "results": [],
                "message": msg,
            })

        # 解析 ProSearch 返回的结果
        inner = data.get("data", {}) or {}
        items = inner.get("docs", data.get("items", [])) or []
        results = []
        for item in items:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", item.get("url", "")),
                "snippet": item.get("snippet", ""),
                "source": item.get("source", ""),
                "time": item.get("time", ""),
            })

        logger.info("搜索结果: %d 条", len(results))
        return web.json_response({
            "query": query,
            "results": results,
            "result_count": len(results),
        })

    except TimeoutError:
        logger.warning("搜索超时: %s", query)
        return web.json_response({
            "query": query,
            "results": [],
            "message": "搜索超时（15秒）",
        })
    except Exception as e:
        logger.error("搜索异常: %s", str(e))
        return web.json_response({
            "query": query,
            "results": [],
            "message": f"搜索异常: {str(e)[:200]}",
        })


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

    app = web.Application()
    app.router.add_post("/search", handle_search)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_health)

    logger.info("搜索代理启动于端口 %d", port)
    logger.info("API: POST http://localhost:%d/search", port)
    logger.info("     {\"query\": \"搜索关键词\", \"count\": 8}")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    # 保持运行
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
