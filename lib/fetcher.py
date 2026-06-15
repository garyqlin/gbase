# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/fetcher.py

统一的 HTTP 抓取器：代理池 + 自动重试 + headers 伪装。
所有引擎和 fetch_page 共用此组件。
"""

import asyncio
import logging
import random

import httpx

logger = logging.getLogger(__name__)

# ── 用户代理池 ──

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


# ── 代理配置 ──

# 代理链（按优先级降序）
# 可以添加 SOCKS5 代理或 HTTP 代理在这里
PROXY_URLS = [
    # 示例: "socks5://127.0.0.1:1080",
    # 示例: "http://proxy.example.com:8080",
]


# ── 抓取器 ──


class Fetcher:
    """统一的 HTTP 抓取器。

    会自动配置 headers、代理、超时、重试。
    """

    def __init__(self, timeout: int = 15, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries
        self._session: httpx.AsyncClient | None = None

    async def _get_session(self) -> httpx.AsyncClient:
        """获取或创建共享 session。"""
        if self._session is None or self._session.is_closed:
            transport = httpx.AsyncHTTPTransport(retries=0)
            self._session = httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
            )
        return self._session

    async def fetch(self, url: str, timeout: int | None = None) -> str | None:
        """抓取一个 URL，返回文本内容。

        自动处理：
        - User-Agent 随机
        - 重试（最多 max_retries 次）
        - 代理（如果配置了）
        - 超时
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
                    # 检测编码：优先用响应头中的 charset，其次自动检测
                    import re as _re

                    raw = resp.content
                    # 从 Content-Type 提取 charset
                    ct = resp.headers.get("content-type", "")
                    charset = None
                    m = _re.search(r"charset=([\w-]+)", ct, _re.I)
                    if m:
                        charset = m.group(1).lower()
                    # 从 HTML meta 提取 charset
                    if not charset:
                        head = raw[:4096].decode("utf-8", errors="ignore")
                        m = _re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head, _re.I)
                        if m:
                            charset = m.group(1).lower()
                    # 按检测到的编码解码，默认 utf-8
                    try:
                        if charset and charset not in ("utf-8", "utf8"):
                            return raw.decode(charset, errors="replace")
                        return raw.decode("utf-8", errors="replace")
                    except (LookupError, ValueError):
                        return raw.decode("utf-8", errors="replace")
                elif resp.status_code in (429, 503):
                    # 被限流，等一秒重试
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
                    await asyncio.sleep(0.5 * (attempt + 1))  # 递增等待
                    continue

        logger.warning("fetch %s 重试 %d 次后失败: %s", url, self.max_retries, last_error)
        return None

    async def fetch_json(self, url: str, timeout: int | None = None) -> dict | None:
        """抓取 JSON API。"""
        text = await self.fetch(url, timeout=timeout)
        if text is None:
            return None
        try:
            import json

            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def close(self):
        """关闭 session。"""
        if self._session and not self._session.is_closed:
            await self._session.aclose()

    def __del__(self):
        try:
            import asyncio

            if self._session and not self._session.is_closed:
                asyncio.create_task(self._session.aclose())
        except Exception:
            pass


# ── 全局默认 fetcher ──

DEFAULT_FETCHER = Fetcher()
