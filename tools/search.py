# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/search.py

Web search: multi-engine parallel + BeautifulSoup parsing + auto snippet + cache
"""

import asyncio
import logging
import re
import urllib.parse

from bs4 import BeautifulSoup

from lib.fetcher import Fetcher
from lib.toolkit import tool

logger = logging.getLogger(__name__)

# Search engine configuration


class SearchEngine:
    """Search engine description."""

    def __init__(self, name: str, lang: str, weight: float = 1.0):
        self.name = name
        self.lang = lang  # "zh" | "en" | "all"
        self.weight = weight  # Result sort weight


ENGINES = {
    "bing": SearchEngine("Bing", "all", weight=1.5),
    "bing_cn": SearchEngine("Bing CN", "zh", weight=1.3),
    "duckduckgo": SearchEngine("DuckDuckGo", "en", weight=1.0),
    "ddg_lite": SearchEngine("DDG Lite", "en", weight=0.9),
    "google": SearchEngine("Google", "all", weight=2.0),
    "swisscows": SearchEngine("Swisscows", "en", weight=0.7),
    "qwant": SearchEngine("Qwant", "all", weight=0.9),
    "startpage": SearchEngine("Startpage", "en", weight=0.8),
}

# Chinese query default engines
ZH_ENGINES = ["google", "qwant", "duckduckgo"]
# English query default engines
EN_ENGINES = ["google", "ddg_lite", "qwant", "duckduckgo", "startpage"]

ENGINE_MAP: dict[str, callable] = {}


# Cache

_cache: dict[str, dict] = {}
"""In-memory cache: query_hash -> { data, ts }"""


def _is_chinese(query: str) -> bool:
    """Check if query is primarily Chinese."""
    cn = sum(1 for c in query if "\u4e00" <= c <= "\u9fff")
    return cn > len(query) * 0.3


def _query_key(query: str) -> str:
    q = query.lower().strip()
    for ch in "，。！？；：、''（）【】《》?.,!;:'\"()[]":
        q = q.replace(ch, " ")
    return " ".join(q.split())


# Generic HTML parsing helper


def _parse_common(
    soup: BeautifulSoup,
    result_selector: str,
    title_selector: str,
    url_attr: str = "href",
    _title_attr: str | None = None,
    snippet_selector: str | None = None,
    max_results: int = 5,
) -> list[dict]:
    """Generic search result parser.

    Extract result listings using BeautifulSoup CSS selectors.
    """
    results: list[dict] = []
    for item in soup.select(result_selector)[:max_results]:
        title_el = item.select_one(title_selector) if title_selector else None
        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        url = title_el.get(url_attr, "")
        if not url or not title:
            continue

        # Clean URL
        if url.startswith("//"):
            url = "https:" + url

        entry = {"title": title[:120], "url": url, "snippet": ""}

        if snippet_selector:
            snip = item.select_one(snippet_selector)
            if snip:
                entry["snippet"] = snip.get_text(strip=True)[:400]

        results.append(entry)
    return results


# Engine implementations


async def _bing(query: str, fetcher: Fetcher, market: str = "en-US") -> list[dict]:
    """Bing search."""
    url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&setlang={market[:2].lower()}&cc={market[:2].lower()}"
    if market.startswith("zh"):
        url += "&mkt=zh-CN"
    html = await fetcher.fetch(url, timeout=10)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")

    return _parse_common(
        soup,
        result_selector="li.b_algo",
        title_selector="h2 a",
        snippet_selector=".b_lineclamp2, .b_caption p, .b_algo p",
        max_results=5,
    )


async def _ddg(query: str, fetcher: Fetcher) -> list[dict]:
    """DuckDuckGo zero-click API."""
    url = "https://api.duckduckgo.com/"
    params = {"q": query, "format": "json", "skip_disambig": 1, "no_html": 1}
    data = await fetcher.fetch_json(f"{url}?{urllib.parse.urlencode(params)}", timeout=10)
    if not data:
        return []

    results = []
    abstract = data.get("AbstractText", "")
    if abstract:
        results.append(
            {
                "title": (data.get("AbstractSource", "") + " - " + abstract[:50])[:120],
                "url": data.get("AbstractURL", ""),
                "snippet": abstract[:400],
            }
        )

    for topic in data.get("RelatedTopics", [])[:5]:
        if isinstance(topic, dict) and "Text" in topic:
            results.append(
                {
                    "title": topic["Text"][:120],
                    "url": topic.get("FirstURL", "") or "",
                    "snippet": topic["Text"][:400],
                }
            )
        elif isinstance(topic, dict) and "Topics" in topic:
            for sub in topic["Topics"][:2]:
                if isinstance(sub, dict) and "Text" in sub:
                    results.append(
                        {
                            "title": sub["Text"][:120],
                            "url": sub.get("FirstURL", "") or "",
                            "snippet": sub["Text"][:400],
                        }
                    )
    return results[:5]


async def _swisscows(query: str, fetcher: Fetcher) -> list[dict]:
    """Swisscows search."""
    url = f"https://swisscows.com/web?query={urllib.parse.quote(query)}"
    html = await fetcher.fetch(url, timeout=10)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_common(
        soup,
        result_selector=".result-item, .web-result, [class*=result]",
        title_selector="a.title, h3 a, a[href]",
        snippet_selector=".description, p.description, .snippet, p",
        max_results=5,
    )


async def _google(query: str, fetcher: Fetcher) -> list[dict]:
    """Google search via HTML."""
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    html = await fetcher.fetch(url, timeout=10)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_common(
        soup,
        result_selector="div.g, div[jscontroller], div[data-hveid]",
        title_selector="h3 a, a[href^='http'] h3",
        snippet_selector=".VwiC3b, .lEBKkf, span.aCOpRe",
        max_results=5,
    )


async def _ddg_lite(query: str, fetcher: Fetcher) -> list[dict]:
    """DuckDuckGo Lite (pure HTML, no JS dependency)."""
    url = f"https://lite.duckduckgo.com/lite?q={urllib.parse.quote(query)}"
    html = await fetcher.fetch(url, timeout=10)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    results = []
    # DDG Lite table layout: <tr class="result"><td class="result-snippet">...</td></tr>
    for row in soup.select("tr.result")[:5]:
        snippet_td = row.select_one("td.result-snippet")
        if snippet_td:
            a = snippet_td.select_one("a")
            if a:
                title = a.get_text(strip=True)[:120]
                href = a.get("href", "")
                snippet = ""
                frag = snippet_td.select_one(".snippet")
                if frag:
                    snippet = frag.get_text(strip=True)[:400]
                else:
                    a_text = a.get_text() if a else ""
                    all_text = snippet_td.get_text()
                    extra = all_text.replace(a_text, "", 1).strip()
                    if extra:
                        snippet = extra[:400]
                results.append({"title": title, "url": href, "snippet": snippet})
    return results


async def _qwant(query: str, fetcher: Fetcher) -> list[dict]:
    """Qwant search (HTML version, no API key)."""
    url = f"https://www.qwant.com/?q={urllib.parse.quote(query)}"
    html = await fetcher.fetch(url, timeout=10)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_common(
        soup,
        result_selector="a.result, [class*=result], [data-testid=result]",
        title_selector="a, h2 a, h3 a",
        snippet_selector="p, [class*=desc], [class*=snippet], [class*=summary]",
        max_results=5,
    )


async def _startpage(query: str, fetcher: Fetcher) -> list[dict]:
    """Startpage search (privacy search engine, HTML based)."""
    url = f"https://www.startpage.com/do/search?q={urllib.parse.quote(query)}"
    html = await fetcher.fetch(url, timeout=10)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_common(
        soup,
        result_selector=".result, .w-gl__result, .layout-basic__item",
        title_selector="h3 a, .result-title a, a.result-title",
        snippet_selector=".result-description, .description, .w-gl__description",
        max_results=5,
    )


# Register engine mapping

ENGINE_MAP["bing"] = _bing
ENGINE_MAP["bing_cn"] = lambda q, f: _bing(q, f, "zh-CN")
ENGINE_MAP["duckduckgo"] = _ddg
ENGINE_MAP["google"] = _google
ENGINE_MAP["ddg_lite"] = _ddg_lite
ENGINE_MAP["swisscows"] = _swisscows
ENGINE_MAP["qwant"] = _qwant
ENGINE_MAP["startpage"] = _startpage


# Parallel search


async def _parallel_search(engines: list[str], query: str, fetcher: Fetcher) -> list[dict]:
    """Call multiple search engines in parallel, return merged deduplicated results."""
    tasks = {}
    for name in engines:
        func = ENGINE_MAP.get(name)
        if func:
            tasks[name] = asyncio.create_task(func(query, fetcher))
        else:
            logger.warning("Unknown engine: %s", name)

    all_results: list[dict] = []
    seen_urls: set[str] = set()

    for name, task in tasks.items():
        try:
            results = await asyncio.wait_for(task, timeout=5)
            for r in results:
                url = r.get("url", "").split("?")[0].split("#")[0]
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    r["_engine"] = name
                    r["_weight"] = ENGINES.get(name, SearchEngine("?", "?", 0)).weight
                    all_results.append(r)
        except TimeoutError:
            logger.debug("Engine %s timeout", name)
        except Exception as e:
            logger.debug("Engine %s failed: %s", name, e)

    all_results.sort(key=lambda r: r.get("_weight", 0), reverse=True)
    return all_results


def _get_expansion_queries(query: str) -> list[str]:
    """Generate expanded query variants for better coverage.
    Automatically retry with different expressions when results are sparse.
    """
    expansions = []
    # Homophone substitution for common surnames
    homophones = {
        "Zhou": ["Zhou", "Zou", "Zhong"],
        "Zhang": ["Zhang", "Zang"],
        "Li": ["Li", "Lee", "Lei"],
        "Wang": ["Wang", "Wong"],
        "Chen": ["Chen", "Cheng"],
        "Lin": ["Lin", "Ling"],
        "Wu": ["Wu", "Woo"],
        "Liu": ["Liu", "Liew"],
        "Huang": ["Huang", "Wong"],
        "Zhao": ["Zhao", "Zau"],
        "Yang": ["Yang", "Yeung"],
        "Zhu": ["Zhu", "Chu"],
    }
    for i, ch in enumerate(query):
        if ch in homophones:
            for alt in homophones[ch]:
                if alt != ch:
                    expansions.append(query[:i] + alt + query[i + 1 :])
    return expansions


async def _fetch_body(url: str, fetcher, max_chars: int = 2000) -> str:
    """Generic body fetcher: fetch page HTML, strip tags, return plain text.
    Skip encyclopedia/dictionary pages (snippet is sufficient).
    """
    if not url or not url.startswith("http"):
        return ""
    skip_domains = ["baike.baidu.com", "zdic.net", "hanyuguoxue.com", "hancibao.com", "wenku.so.com"]
    if any(d in url for d in skip_domains):
        return ""
    try:
        text = await fetcher.fetch(url, timeout=10)
        if not text or len(text.strip()) < 300:
            return ""
        soup = BeautifulSoup(text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()
        body = soup.get_text(separator=" ", strip=True)
        body = re.sub(r"\s+", " ", body)
        if len(body) > 100:
            return body[:max_chars]
    except Exception:
        pass
    return ""


@tool()
async def search_web(query: str, engines: list[str] | None = None) -> dict:
    """Search the web and return relevant result summaries.

    Auto-select engines by query language. Supports custom engine list.

    Args:
        query: Search keywords
        engines: Optional list of engine names, e.g. ["google", "qwant"]. Default auto-selects by language.
    """
    # Tunnel first: try SSH reverse tunnel to local ProSearch
    try:
        from tools.search_tunnel import search_via_tunnel

        tunnel_results = await search_via_tunnel(query, 8)
        if tunnel_results:
            logger.info("Tunnel search hit: %s (%d results)", query, len(tunnel_results))
            data = {
                "query": query,
                "result_count": len(tunnel_results),
                "engine_count": 1,
                "results": [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("snippet", ""),
                    }
                    for r in tunnel_results
                ],
                "note": "from ProSearch (SSH tunnel)",
            }
            _cache[_query_key(query)] = {"data": data, "ts": 0}
            return data
    except Exception as e:
        logger.warning("Tunnel search failed (%s), falling back to crawler engines", str(e)[:60])

    # Cache check
    cache_key = _query_key(query)
    cached = _cache.get(cache_key)
    if cached:
        logger.info("Search cache hit: %s", query)
        return cached["data"]

    # Handle engine list (string -> list)
    if isinstance(engines, str):
        engines = [e.strip() for e in re.split(r"[,\s]+", engines) if e.strip()]

    engine_list = engines
    valid_names = set(ENGINES.keys())
    if engine_list:
        engine_list = [e for e in engine_list if e in valid_names]
        if not engine_list:
            logger.warning("All specified engines invalid, falling back to auto-select")

    if not engine_list:
        engine_list = ZH_ENGINES if _is_chinese(query) else EN_ENGINES

    logger.info("Search: %s -> engines: %s", query, " + ".join(engine_list))

    fetcher = Fetcher()
    try:
        results = await _parallel_search(engine_list, query, fetcher)

        # Auto-expand when results < 3
        if len(results) < 3 and len(query) >= 2:
            expansions = _get_expansion_queries(query)
            if expansions:
                logger.info("Results sparse (%d), auto-expanding: %s", len(results), " / ".join(expansions[:2]))
                for eq in expansions[:2]:
                    extra = await _parallel_search(ZH_ENGINES, eq, fetcher)
                    existing_urls = {r.get("url", "") for r in results}
                    for r in extra:
                        if r.get("url", "") not in existing_urls:
                            results.append(r)
                            existing_urls.add(r.get("url", ""))
                logger.info("After expansion: %d results", len(results))

        # Format output
        formatted = []
        for r in results:
            entry = {}
            if r.get("title"):
                entry["title"] = r["title"][:120]
            if r.get("url"):
                entry["url"] = r["url"]
            s = r.get("snippet", "") or ""
            if s:
                entry["snippet"] = s[:800]
            entry["_engine"] = r.get("_engine", "?")
            formatted.append(entry)

        # Auto-fetch top 2 result page bodies
        pages: list[dict] = []
        for r in results[:3]:
            body = await _fetch_body(r.get("url", ""), fetcher, max_chars=2000)
            if body:
                pages.append(
                    {
                        "title": r.get("title", "")[:80],
                        "content": body[:2000],
                    }
                )
                if len(pages) >= 2:
                    break

        data = {
            "query": query,
            "result_count": len(formatted),
            "engine_count": len(engine_list),
            "results": formatted,
            "pages": pages,
            "note": f"from {len(engine_list)} engines parallel search" if len(engine_list) > 1 else "",
        }

        _cache[cache_key] = {"data": data, "ts": asyncio.get_event_loop().time()}
        if len(_cache) > 200:
            old = sorted(_cache.keys(), key=lambda k: _cache[k]["ts"])[:50]
            for k in old:
                del _cache[k]

        return data

    finally:
        await fetcher.close()


@tool()
async def fetch_page(url: str) -> dict:
    """Fetch web page content (text summary version)."""
    fetcher = Fetcher(timeout=20)
    try:
        text = await fetcher.fetch(url, timeout=20)
        if text is None:
            return {"error": "Page fetch failed", "url": url}

        soup = BeautifulSoup(text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        body = soup.get_text(separator="\n", strip=True)

        lines = [line.strip() for line in body.split("\n") if line.strip()]
        text = "\n".join(lines)[:4000]

        return {"url": url, "content": text[:3000]}
    finally:
        await fetcher.close()


@tool()
async def search_self(question: str) -> dict:
    """Search own experience store for accumulated knowledge.

    Use when needing to recall what was done before, what was learned, or how similar problems were handled.

    Args:
        question: Experience question to search for
    """
    from lib import toolkit as tk

    ee = tk.get_global("experience_engine")
    if not ee:
        return {"found": False, "results": [], "note": "Experience engine not ready"}

    try:
        memory = await ee.search(question)
        if memory and len(memory) > 0:
            return {"found": True, "count": len(memory), "results": memory}
        return {"found": False, "results": [], "note": "No matching records found in experience store"}
    except Exception as e:
        logger.warning("search_self failed: %s", e)
        return {"found": False, "results": [], "note": f"Query error: {e}"}


# Single-pass search (for kernel internal use)


async def search_main(query: str) -> dict:
    """Kernel internal: one-shot search across engines + auto body extraction, no LLM involvement.

    Return format:
    {
        "query": str,
        "digest": str,   # Search results + body content digest
        "sources": [],   # Original links
    }
    """
    logger.info("search_main: %s", query)
    _is_chinese(query)

    cn_engines = ZH_ENGINES
    en_engines = ["bing"]

    all_engines = cn_engines + [e for e in en_engines if e not in cn_engines]

    fetcher = Fetcher()
    try:
        results = await _parallel_search(all_engines, query, fetcher)
        if not results:
            return {"query": query, "digest": "", "sources": []}

        # Collect snippets (top 3 per engine)
        from collections import OrderedDict

        extra_parts = []
        by_engine = OrderedDict()
        for r in results:
            eng = r.get("_engine", "?")
            if eng not in by_engine:
                by_engine[eng] = []
            if len(by_engine[eng]) < 3:
                by_engine[eng].append(r)
        for eng, items in by_engine.items():  # noqa: B007
            for r in items:
                s = r.get("snippet", "") or ""
                if s and len(s) > 10:
                    extra_parts.append(f"• {r.get('title', '?')} — {s[:300]}")

        # Body content
        content_parts = []

        # Fetch body from top results
        for r in results[:12]:
            url = r.get("url", "")
            if not url or "baidu.com" in url or "sogou.com" in url or "qcaptcha" in url:
                continue
            if any(d in url for d in ["baike.baidu", "zdic.net", "wenku.so"]):
                continue
            body = await _fetch_body(url, fetcher)
            if body:
                content_parts.append(f"• {r.get('title', '?')}\n{body}")
                break

        # Build digest
        parts = []
        if extra_parts:
            parts.append("[Search Results Snippets]")
            parts.extend(extra_parts[:5])
        if content_parts:
            parts.append("\n[Detailed Content]")
            parts.extend(content_parts[:3])

        digest = "\n\n---\n\n".join(parts)[:4000]

        sources = [r.get("url", "") for r in results[:6] if r.get("url")]

        return {
            "query": query,
            "digest": digest,
            "sources": sources,
        }
    finally:
        await fetcher.close()
