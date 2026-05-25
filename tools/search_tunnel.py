# SPDX-License-Identifier: MIT
"""
search_tunnel.py — Tunnel search bridge
优先调用本地 SSH 隧道 (127.0.0.1:18430) 的 ProSearch，
不可用时 fallback 到原有自爬引擎。
"""

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

TUNNEL_URL = "http://127.0.0.1:18430/search"
TUNNEL_TIMEOUT = 8  # 隧道超时短，快速降级


async def search_via_tunnel(query: str, count: int = 8) -> list[dict] | None:
    """通过 SSH 隧道调用本地 ProSearch，成功返回结果列表，失败返回 None。"""
    import asyncio

    try:
        payload = json.dumps({"query": query, "count": count}).encode("utf-8")

        # 用 asyncio 的线程池执行阻塞 HTTP 请求
        loop = asyncio.get_event_loop()

        def _do_req():
            req = urllib.request.Request(
                TUNNEL_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=TUNNEL_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))

        data = await loop.run_in_executor(None, _do_req)

        if not data:
            return None

        results = data.get("results", [])
        if not results:
            return None

        logger.info("隧道搜索成功: %s -> %d 条", query, len(results))
        return results

    except (urllib.error.URLError, ConnectionRefusedError, TimeoutError, OSError, json.JSONDecodeError) as e:
        logger.warning("隧道搜索失败 (%s)，回退自爬引擎", str(e)[:60])
        return None
    except Exception as e:
        logger.warning("隧道搜索异常 (%s)，回退自爬引擎", str(e)[:60])
        return None
