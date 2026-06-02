# SPDX-License-Identifier: MIT
"""
gbase/lib/fetcher.py

Unified HTTP fetcher: proxy pool + auto-retry + header spoofing.
Shared by all engines and fetch_page.
"""

import asyncio
import logging
import random

import httpx

logger = logging.getLogger(__name__)

# ── User-Agent Pool ──

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


# ── Proxy Config ──

# Proxy chain (descending priority)
# Add SOCKS5 or HTTP proxies here
PROXY_URLS = [
    # Example: "socks5://127.0.0.1:1080",
    # Example: "http://proxy.example.com:8080",
]


# ── Fetcher ──


class Fetcher:
    """Unified HTTP fetcher.

    Auto-configures headers, proxies, timeout, and retries.
    """

    def __init__(self, timeout: int = 15, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries
        self._session: httpx.AsyncClient | None = None

    async def _get_session(self) -> httpx.AsyncClient:
        """Get or create a shared session."""
        if self._session is None or self._session.is_closed:
            transport = httpx.AsyncHTTPTransport(retries=0)
            self._session = httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
            )
        return self._session

    async def fetch(self, url: str, timeout: int | None = None) -> str | None:
        """Fetch a URL and return text content.

        Handles automatically:
        - Random User-Agent
        - Retries (up to max_retries times)
        - Proxies (if configured)
        - Timeout
        """
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        last_error = None
        t = timeout or self.timeout

        for attempt in range(1 + self.max_retries):
            try:
                session = await self._get_session()

                resp = await session.get(
                    url,
                    headers=headers,
                    timeout=t,
                )

                if resp.status_code == 200:
                    # Detect encoding: prefer charset from response header, then auto-detect
                    import re as _re

                    raw = resp.content
                    # Extract charset from Content-Type
                    ct = resp.headers.get("content-type", "")
                    charset = None
                    m = _re.search(r"charset=([\w-]+)", ct, _re.I)
                    if m:
                        charset = m.group(1).lower()
                    # Extract charset from HTML meta
                    if not charset:
                        head = raw[:4096].decode("utf-8", errors="ignore")
                        m = _re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head, _re.I)
                        if m:
                            charset = m.group(1).lower()
                    # Decode with detected encoding, default utf-8
                    try:
                        if charset and charset not in ("utf-8", "utf8"):
                            return raw.decode(charset, errors="replace")
                        return raw.decode("utf-8", errors="replace")
                    except (LookupError, ValueError):
                        return raw.decode("utf-8", errors="replace")
                elif resp.status_code in (429, 503):
                    # Rate limited, wait 1s and retry
                    await asyncio.sleep(1)
                    continue
                else:
                    logger.debug("fetch %s status=%d (attempt %d)", url, resp.status_code, attempt)
                    if attempt < self.max_retries:
                        await asyncio.sleep(0.5)
                        continue
                    return None

            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_error = e
                logger.debug("fetch %s attempt %d failed: %s", url, attempt + 1, e)
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))  # Incremental backoff
                    continue

        logger.warning("fetch %s failed after %d retries: %s", url, self.max_retries, last_error)
        return None

    async def fetch_json(self, url: str, timeout: int | None = None) -> dict | None:
        """Fetch JSON API."""
        text = await self.fetch(url, timeout=timeout)
        if text is None:
            return None
        try:
            import json

            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def close(self):
        """Close the session."""
        if self._session and not self._session.is_closed:
            await self._session.aclose()

    def __del__(self):
        try:
            import asyncio

            if self._session and not self._session.is_closed:
                asyncio.create_task(self._session.aclose())
        except Exception:
            pass


# ── Global Default Fetcher ──

DEFAULT_FETCHER = Fetcher()
