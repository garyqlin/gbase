# SPDX-License-Identifier: MIT
"""
search_tunnel.py — Tunnel search bridge
Prioritize calling ProSearch via local SSH tunnel (127.0.0.1:18430),
fallback to existing self-crawling engine when unavailable.
"""

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

TUNNEL_URL = "http://127.0.0.1:18430/search"
TUNNEL_TIMEOUT = 8  # Short tunnel timeout for fast degradation


async def search_via_tunnel(query: str, count: int = 8) -> list[dict] | None:
    """Call local ProSearch via SSH tunnel. Returns result list on success, None on failure."""
    import asyncio

    try:
        payload = json.dumps({"query": query, "count": count}).encode("utf-8")

        # Execute blocking HTTP request via asyncio thread pool
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

        logger.info("Tunnel search success: %s -> %d results", query, len(results))
        return results

    except (urllib.error.URLError, ConnectionRefusedError, TimeoutError, OSError, json.JSONDecodeError) as e:
        logger.warning("Tunnel search failed (%s), falling back to self-crawling engine", str(e)[:60])
        return None
    except Exception as e:
        logger.warning("Tunnel search exception (%s), falling back to self-crawling engine", str(e)[:60])
        return None
