#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
honeycomb_search.py — Multi-wave meta search engine

Three-wave blanket search + gap analysis + multi-dimension fusion.
Core design:
  1. Dimension matrix: 6 search dimensions covered simultaneously
  2. Wave search: search -> analyze gaps -> re-search -> analyze again -> vertical deep-dive
  3. Gap detection: auto-identify uncovered angles and generate expansion queries

Usage:
    python3 honeycomb_search.py --query="LBS casting inspection standards" --depth=full
    python3 honeycomb_search.py --query="AI agent framework 2026" --depth=normal
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

from lib.toolkit import tool

# Logger
logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("honeycomb")

# Available engine weights
ENGINE_WEIGHTS = {
    "prosearch": 0.99,  # ProSearch — stable primary engine
    "bing": 0.85,
    "google": 0.95,
    "duckduckgo": 0.75,
    "qwant": 0.65,
    "brave": 0.70,
    "zhihu": 0.40,
    "github": 0.75,
    "arxiv": 0.80,
    "hackernews": 0.50,
}

# Search dimension definitions
# Each dimension: engine list + max results + expansion angle keywords
# ProSearch as primary engine covers all dimensions
# Other engines bring diversity from different sources
DIMENSIONS = {
    "general": {
        "engines": ["prosearch"],
        "max_per_engine": 12,
        "priority": "required",
        "expansion_keywords": [],
    },
    "chinese": {
        "engines": ["prosearch", "google"],
        "max_per_engine": 4,
        "priority": "required",
        "expansion_keywords": [],
    },
    "tech": {
        "engines": ["prosearch"],
        "max_per_engine": 8,
        "priority": "p1",
        "expansion_keywords": ["implementation", "github", "paper", "survey"],
    },
    "social": {
        "engines": ["prosearch", "google"],
        "max_per_engine": 4,
        "priority": "p1",
        "expansion_keywords": ["discussion", "opinion", "review", "recommendation"],
    },
    "authority": {
        "engines": ["prosearch"],
        "max_per_engine": 6,
        "priority": "p2",
        "expansion_keywords": ["standard", "regulation", "official", "government", "wiki"],
    },
    "longtail": {
        "engines": ["prosearch"],
        "max_per_engine": 6,
        "priority": "p2",
        "expansion_keywords": [],
    },
}

# Wave configuration
WAVES = {
    "quick": [
        {"name": "wave1_broad", "dimensions": ["general", "chinese"], "max_total": 20},
    ],
    "normal": [
        {"name": "wave1_broad", "dimensions": ["general", "chinese"], "max_total": 20},
        {"name": "wave2_deep", "dimensions": ["tech", "social"], "max_total": 15},
    ],
    "full": [
        {"name": "wave1_broad", "dimensions": ["general", "chinese"], "max_total": 25},
        {"name": "wave2_deep", "dimensions": ["tech", "social"], "max_total": 20},
        {"name": "wave3_vertical", "dimensions": ["authority", "longtail"], "max_total": 15},
    ],
}

# Angle keywords for gap analysis
ANGLE_KEYWORDS = {
    "definition": ["definition", "overview", "introduction", "what is"],
    "status": ["status", "progress", "trend", "current", "state", "news"],
    "technology": ["technology", "method", "tool", "framework", "implementation", "code"],
    "standards": ["standard", "specification", "regulation"],
    "market": ["market", "size", "data", "statistics", "survey"],
    "opinions": ["opinion", "review", "discussion", "comparison"],
    "cases": ["case study", "use case", "example", "application", "practice"],
    "future": ["future", "direction", "outlook", "challenge"],
}


@dataclass
class SearchResult:
    """Single search result"""
    title: str
    url: str
    snippet: str = ""
    source: str = ""
    dimension: str = ""
    weight: float = 0.5
    language: str = "unknown"

    def __post_init__(self):
        chinese_chars = sum(1 for c in self.title + self.snippet if '\u4e00' <= c <= '\u9fff')
        self.language = "zh" if chinese_chars > 3 else "en"


def _is_chinese(text: str) -> bool:
    return sum(1 for c in text if '\u4e00' <= c <= '\u9fff') > 3


def _quote(text: str) -> str:
    return quote_plus(text)


async def _fetch_url(url: str, timeout: int = 6) -> str | None:
    """Generic URL fetcher, returns HTML text."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        r = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=timeout)),
            timeout=timeout + 2,
        )
        return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug("fetch failed: %s -> %s", url, str(e)[:40])
        return None


# ---------------- Engine implementations ----------------


async def _search_bing(query: str, max_results: int = 8) -> list[SearchResult]:
    html = await _fetch_url(f"https://www.bing.com/search?q={_quote(query)}&count={max_results}")
    if not html:
        return []
    results = []
    for m in re.finditer(r'<li class="b_algo"[^>]*>.*?<h2>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>', html, re.DOTALL):
        url, title, snippet = m.group(1), re.sub(r'<[^>]+>', '', m.group(2)).strip(), re.sub(r'<[^>]+>', '', m.group(3)).strip()
        if url:
            results.append(SearchResult(title=title, url=url, snippet=snippet, source="bing"))
            if len(results) >= max_results:
                break
    return results


async def _search_ddg(query: str, max_results: int = 6) -> list[SearchResult]:
    """DuckDuckGo HTML version"""
    html = await _fetch_url(f"https://html.duckduckgo.com/html/?q={_quote(query)}")
    if not html:
        return []
    results = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</(?:a|td)', html, re.DOTALL):
        url = m.group(1) if m.group(1).startswith("http") else ""
        title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        snippet = re.sub(r'<[^>]+>', '', m.group(3)).strip()
        if url:
            results.append(SearchResult(title=title, url=url, snippet=snippet, source="duckduckgo"))
            if len(results) >= max_results:
                break
    return results


async def _search_google(query: str, max_results: int = 6) -> list[SearchResult]:
    """Google search via HTML scraping."""
    html = await _fetch_url(f"https://www.google.com/search?q={_quote(query)}&num={max_results}")
    if not html:
        return []
    results = []
    for m in re.finditer(r'<h3[^>]*>.*?<a[^>]*href="(/url\?q=[^"&]+|https?://[^"]+)"[^>]*>(.*?)</a>.*?</h3>', html, re.DOTALL):
        href = m.group(1)
        title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        url = href
        if href.startswith("/url?q="):
            url = href.split("/url?q=")[1].split("&")[0]
        url = urllib.parse.unquote(url) if url else ""
        if url and url.startswith("http") and len(title) > 3:
            results.append(SearchResult(title=title, url=url, snippet="", source="google"))
            if len(results) >= max_results:
                break
    return results


async def _search_qwant(query: str, max_results: int = 6) -> list[SearchResult]:
    """Qwant API"""
    api_url = f"https://api.qwant.com/v3/search/web?q={_quote(query)}&count={max_results}&locale=en_US"
    try:
        r = await _fetch_url(api_url)
        if r:
            data = json.loads(r)
            items = data.get("data", {}).get("result", {}).get("items", {}).get("mainline", [])
            results = []
            for item in items:
                for sub in item.get("items", []):
                    results.append(SearchResult(
                        title=sub.get("title", ""),
                        url=sub.get("url", ""),
                        snippet=sub.get("desc", ""),
                        source="qwant",
                    ))
            return results
    except:
        pass
    return []


async def _search_prosearch(query: str, max_results: int = 10) -> list[SearchResult]:
    """ProSearch — most stable CH/EN search channel.
    Uses local prosearch.cjs Node.js script via Auth Gateway.
    """
    script_path = os.path.expanduser("~/.qclaw/skills/online-search/scripts/prosearch.cjs")
    if not os.path.exists(script_path):
        logger.warning("prosearch.cjs not found: %s", script_path)
        return []

    try:
        proc = await asyncio.create_subprocess_exec(
            "node", script_path, f"--keyword={query}", f"--cnt={min(max_results, 15)}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode != 0:
            logger.warning("prosearch failed (exit=%d): %s", proc.returncode, (stderr or b"").decode("utf-8", errors="replace")[:100])
            return []

        raw = stdout.decode("utf-8", errors="replace")
        data = json.loads(raw)
        if not data.get("success"):
            return []

        docs = data.get("data", {}).get("docs", [])
        results = []
        for doc in docs:
            title = doc.get("title", "")
            url = doc.get("url", "")
            snippet = doc.get("passage", "")[:200]
            if url:
                results.append(SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source="prosearch",
                ))
                if len(results) >= max_results:
                    break

        logger.info("prosearch: %s -> %d results", query, len(results))
        return results

    except TimeoutError:
        logger.warning("prosearch timeout (15s): %s", query)
        return []
    except Exception as e:
        logger.warning("prosearch error: %s", str(e)[:60])
        return []


# ---------------- Gap analysis ----------------


def _analyze_gaps(query: str, results: list[SearchResult]) -> dict:
    """Analyze information gaps in collected results, generate expansion queries"""
    # 1. Aggregate text
    all_text = " ".join(f"{r.title} {r.snippet}" for r in results)

    # 2. Detect covered angles
    covered_angles = set()
    for angle, keywords in ANGLE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in all_text.lower():
                covered_angles.add(angle)
                break

    # 3. Uncovered angles
    all_angles = set(ANGLE_KEYWORDS.keys())
    uncovered = all_angles - covered_angles

    # 4. Detect language coverage
    has_zh = any(r.language == "zh" for r in results)
    has_en = any(r.language == "en" for r in results)

    # 5. Source diversity
    sources_used = set(r.source for r in results)

    # 6. Generate expansion queries
    expansions = []
    for angle in uncovered:
        kw = ANGLE_KEYWORDS[angle][0]
        expansions.append(f"{query} {kw}")

    if not has_en:
        expansions.append(query)
    if not has_zh:
        expansions.append(query)

    return {
        "covered_angles": sorted(covered_angles),
        "uncovered_angles": sorted(uncovered),
        "has_zh": has_zh,
        "has_en": has_en,
        "sources_used": sorted(sources_used),
        "coverage_rate": round(len(covered_angles) / len(all_angles) * 100, 1),
        "expansions": expansions[:6],
        "is_saturated": len(uncovered) <= 1 and has_zh and has_en,
    }


# ---------------- Result fusion ----------------


def _fuse_results(results: list[SearchResult]) -> dict:
    """Deduplicate + group by dimension + sort"""
    seen = set()
    unique = []
    by_dimension = {}
    dimension_order = list(DIMENSIONS.keys())

    for r in results:
        key = r.url.split("?")[0].split("#")[0]
        if key and key not in seen:
            seen.add(key)
            dim = "general"
            for d in dimension_order:
                if r.source in DIMENSIONS[d]["engines"]:
                    dim = d
                    break
            r.dimension = dim
            unique.append(r)
            if dim not in by_dimension:
                by_dimension[dim] = []
            by_dimension[dim].append(r)

    def sort_key(r):
        dim_idx = dimension_order.index(r.dimension) if r.dimension in dimension_order else 99
        return (dim_idx, -ENGINE_WEIGHTS.get(r.source, 0))

    unique.sort(key=sort_key)

    return {
        "total_raw": len(results),
        "total_unique": len(unique),
        "by_dimension": {d: [{"title": r.title, "url": r.url, "snippet": r.snippet[:200], "source": r.source, "lang": r.language} for r in items] for d, items in by_dimension.items()},
        "dimension_summary": {d: len(items) for d, items in by_dimension.items()},
    }


# ---------------- Honeycomb search main engine ----------------

# Engine routing table
ENGINE_ROUTER = {
    "prosearch": _search_prosearch,
    "bing": _search_bing,
    "duckduckgo": _search_ddg,
    "google": _search_google,
    "qwant": _search_qwant,
}


async def _execute_wave(wave_cfg: dict, query: str, expansions: list[str] = None) -> list[SearchResult]:
    """Execute one search wave"""
    queries = [query]
    if expansions:
        queries.extend(expansions[:3])

    all_results = []
    seen_urls = set()

    for q in queries:
        tasks = []
        for dim in wave_cfg["dimensions"]:
            dim_cfg = DIMENSIONS.get(dim)
            if not dim_cfg:
                continue
            for engine in dim_cfg["engines"]:
                handler = ENGINE_ROUTER.get(engine)
                if handler:
                    tasks.append((dim, engine, handler(q, dim_cfg["max_per_engine"])))

        for dim, engine, coro in tasks:
            try:
                results = await asyncio.wait_for(coro, timeout=8)
                for r in results:
                    key = r.url.split("?")[0]
                    if key not in seen_urls:
                        seen_urls.add(key)
                        r.source = engine
                        all_results.append(r)
            except Exception as e:
                logger.debug("Wave engine failed %s/%s: %s", dim, engine, str(e)[:30])

    all_results.sort(key=lambda r: -ENGINE_WEIGHTS.get(r.source, 0))
    max_total = wave_cfg.get("max_total", 100)
    return all_results[:max_total]


@tool()
async def honeycomb_search(query: str, depth: str = "normal") -> dict:
    """
    Honeycomb search — multi-wave meta search engine.
    Multi-dimension, multi-wave, smart expansion covering 6 dimensions.
    Supports depth: quick(1 wave)/normal(2 waves)/full(3 waves).
    After each wave, automatically analyzes information gaps and generates expansion queries.
    """
    start = time.time()
    waves = WAVES.get(depth, WAVES["normal"])

    all_results = []
    wave_log = []
    expansions = []

    logger.info("Honeycomb search started: query=%s depth=%s", query, depth)

    for i, wave in enumerate(waves):
        wave_results = await _execute_wave(wave, query, expansions)
        all_results.extend(wave_results)

        if i == 0 or i == len(waves) - 1:
            gap = _analyze_gaps(query, all_results)
            expansions = gap["expansions"]

            wave_log.append({
                "wave": wave["name"],
                "engine_count": len(set(r.source for r in wave_results)),
                "result_count": len(wave_results),
                "coverage": gap["coverage_rate"],
                "is_saturated": gap["is_saturated"],
                "uncovered": gap["uncovered_angles"],
            })

            logger.info("Wave %d complete: %s, coverage=%s%%, saturated=%s",
                       i+1, wave["name"], gap["coverage_rate"], gap["is_saturated"])

            if gap["is_saturated"] and i < len(waves) - 1:
                logger.info("Information saturated, terminating search early")
                wave_log.append({"note": "Information saturated, early termination"})
                break

    fused = _fuse_results(all_results)
    elapsed = round(time.time() - start, 2)

    return {
        "query": query,
        "depth": depth,
        "elapsed_s": elapsed,
        "waves": wave_log,
        "results": fused,
    }


# ---------------- CLI entry point ----------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Honeycomb meta search engine")
    parser.add_argument("--query", "-q", required=True, help="Search keywords")
    parser.add_argument("--depth", "-d", default="normal", choices=["quick", "normal", "full"])
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = asyncio.run(honeycomb_search(args.query, args.depth))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"Honeycomb search: {result['query']}")
        print(f"Depth: {result['depth']} | Elapsed: {result['elapsed_s']}s")
        print(f"{'='*60}")

        for wave in result["waves"]:
            r = wave.get("result_count", 0)
            c = wave.get("coverage", 0)
            s = "[OK] " if wave.get("is_saturated") else "[...] "
            print(f"\n{s}Wave '{wave.get('wave', '?')}': {r} results, coverage {c}%, gaps: {wave.get('uncovered', [])}")

        fused = result["results"]
        print(f"\nSummary: {fused['total_raw']} raw -> {fused['total_unique']} unique")

        for dim, items in sorted(fused["by_dimension"].items(), key=lambda x: -len(x[1])):
            print(f"\n  -- {dim} ({len(items)} items) --")
            for item in items[:3]:
                print(f"    * {item['title'][:60]}")
                print(f"      {item['url'][:70]}")
