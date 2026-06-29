#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
honeycomb_search.py — Universal 蜂巢搜索引擎

三波次地毯式搜索 + 智能缺口分析 + 多维结果融合。
核心设计：
  1. 维度矩阵：6 个搜索维度同时覆盖
  2. 波次搜索：搜→分析缺口→再搜→再分析→垂直攻坚
  3. 缺口检测：自动识别未覆盖角度并生成扩展查询

用法：
    python3 honeycomb_search.py --query="LBS 铸造探伤 行业标准" --depth=full
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

# ── 日志 ──
logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("honeycomb")

# ── 可用引擎权重 ──
ENGINE_WEIGHTS = {
    "prosearch": 0.99,  # 元宝ProSearch — 稳定主引擎
    "bing": 0.85,
    "google": 0.80,
    "duckduckgo": 0.75,
    "qwant": 0.65,
    "brave": 0.70,
    "baidu": 0.60,
    "sogou": 0.55,
    "360": 0.50,
    "toutiao": 0.45,
    "zhihu": 0.40,
    "github": 0.75,
    "arxiv": 0.80,
    "hackernews": 0.50,
}

# ── 搜索维度定义 ──
# 每个维度：引擎列表 + 最大结果数 + 扩展角度关键词
# ProSearch 作为主引擎覆盖所有维度（稳定可靠）
# 其他引擎作为补充，带来不同来源的多样性
DIMENSIONS = {
    "general": {
        "engines": ["prosearch"],
        "max_per_engine": 12,
        "priority": "required",
        "expansion_keywords": [],
    },
    "chinese": {
        "engines": ["prosearch", "baidu", "sogou", "360"],
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
        "engines": ["prosearch", "toutiao"],
        "max_per_engine": 4,
        "priority": "p1",
        "expansion_keywords": ["讨论", "观点", "看法", "推荐"],
    },
    "authority": {
        "engines": ["prosearch"],
        "max_per_engine": 6,
        "priority": "p2",
        "expansion_keywords": ["标准", "规范", "官方", "政府", "wiki"],
    },
    "longtail": {
        "engines": ["prosearch"],
        "max_per_engine": 6,
        "priority": "p2",
        "expansion_keywords": [],
    },
}

# ── 波次配置 ──
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

# ── 用于缺口分析的角度词 ──
ANGLE_KEYWORDS = {
    "定义": ["定义", "是什么", "概念", "overview", "introduction", "什么是"],
    "现状": ["现状", "进展", "趋势", "current", "state", "trend", "news"],
    "技术": ["技术", "方法", "工具", "framework", "tool", "implementation", "code"],
    "标准": ["标准", "规范", "规定", "standard", "specification", "regulation"],
    "市场": ["市场", "规模", "数据", "market", "data", "statistics", "survey"],
    "观点": ["观点", "评价", "评论", "opinion", "review", "discussion", "对比"],
    "案例": ["案例", "应用", "实践", "案例研究", "use case", "case study", "example"],
    "探讨": ["前景", "未来", "方向", "future", "direction", "outlook", "challenge"],
}


@dataclass
class SearchResult:
    """单条搜索结果"""

    title: str
    url: str
    snippet: str = ""
    source: str = ""
    dimension: str = ""
    weight: float = 0.5
    language: str = "unknown"

    def __post_init__(self):
        # 语言检测（简单版）
        chinese_chars = sum(1 for c in self.title + self.snippet if "\u4e00" <= c <= "\u9fff")
        self.language = "zh" if chinese_chars > 3 else "en"


def _is_chinese(text: str) -> bool:
    return sum(1 for c in text if "\u4e00" <= c <= "\u9fff") > 3


def _quote(text: str) -> str:
    return quote_plus(text)


async def _fetch_url(url: str, timeout: int = 6) -> str | None:
    """通用 URL 抓取，返回 HTML 文本。"""
    import urllib.request

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        r = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=timeout)),
            timeout=timeout + 2,
        )
        return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug("fetch failed: %s → %s", url, str(e)[:40])
        return None


# ──────────────── 引擎实现 ────────────────


async def _search_bing(query: str, max_results: int = 8) -> list[SearchResult]:
    html = await _fetch_url(f"https://www.bing.com/search?q={_quote(query)}&count={max_results}")
    if not html:
        return []
    results = []
    # Bing 结果格式: <li class="b_algo"> → <h2><a href="..." ...>title</a></h2> → <p class="b_lineclamp2">snippet</p>
    for m in re.finditer(
        r'<li class="b_algo"[^>]*>.*?<h2>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>', html, re.DOTALL
    ):
        url, title, snippet = (
            m.group(1),
            re.sub(r"<[^>]+>", "", m.group(2)).strip(),
            re.sub(r"<[^>]+>", "", m.group(3)).strip(),
        )
        if url:
            results.append(SearchResult(title=title, url=url, snippet=snippet, source="bing"))
            if len(results) >= max_results:
                break
    return results


async def _search_ddg(query: str, max_results: int = 6) -> list[SearchResult]:
    """DuckDuckGo HTML 版"""
    html = await _fetch_url(f"https://html.duckduckgo.com/html/?q={_quote(query)}")
    if not html:
        return []
    results = []
    for m in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</(?:a|td)',
        html,
        re.DOTALL,
    ):
        url = m.group(1) if m.group(1).startswith("http") else ""
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        snippet = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        if url:
            results.append(SearchResult(title=title, url=url, snippet=snippet, source="duckduckgo"))
            if len(results) >= max_results:
                break
    return results


async def _search_baidu(query: str, max_results: int = 6) -> list[SearchResult]:
    html = await _fetch_url(f"https://www.baidu.com/s?wd={_quote(query)}&rn={max_results}")
    if not html:
        return []
    results = []
    for m in re.finditer(
        r'<div[^>]*class="[^"]*result[^"]*"[^>]*>.*?<h3[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<span[^>]*class="content-right_[^"]*"[^>]*>(.*?)</span>',
        html,
        re.DOTALL,
    ):
        url = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        snippet = re.sub(r"<[^>]+>", "", m.group(3)).strip() if m.group(3) else ""
        if url:
            results.append(SearchResult(title=title, url=url, snippet=snippet, source="baidu"))
            if len(results) >= max_results:
                break
    # 备用：搜不到走简单的 match
    if not results:
        for m in re.finditer(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', html):
            url = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if "baidu" not in url and len(title) > 5 and "百度" not in title:
                results.append(SearchResult(title=title, url=url, snippet="", source="baidu"))
                if len(results) >= max_results:
                    break
    return results


async def _search_sogou(query: str, max_results: int = 6) -> list[SearchResult]:
    html = await _fetch_url(f"https://www.sogou.com/web?query={_quote(query)}")
    if not html:
        return []
    results = []
    for m in re.finditer(
        r'<h3[^>]*>.*?<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>.*?</h3>\s*<p[^>]*>(.*?)</p>', html, re.DOTALL
    ):
        url = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        snippet = re.sub(r"<[^>]+>", "", m.group(3)).strip() if m.group(3) else ""
        if url:
            results.append(SearchResult(title=title, url=url, snippet=snippet, source="sogou"))
            if len(results) >= max_results:
                break
    return results


async def _search_360(query: str, max_results: int = 6) -> list[SearchResult]:
    html = await _fetch_url(f"https://www.so.com/s?q={_quote(query)}")
    if not html:
        return []
    results = []
    for m in re.finditer(r'class="res-list"[^>]*>.*?<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
        url = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if url and url.startswith("http"):
            results.append(SearchResult(title=title, url=url, snippet="", source="360"))
            if len(results) >= max_results:
                break
    return results


async def _search_toutiao(query: str, max_results: int = 5) -> list[SearchResult]:
    html = await _fetch_url(f"https://so.toutiao.com/search?dvpf=pc&source=input&keyword={_quote(query)}")
    if not html:
        return []
    results = []
    # 头条搜索结果
    for m in re.finditer(r'"title":"([^"]+)","url":"([^"]+)"', html):
        title = m.group(1).replace("\\u003c", "<").replace("\\u003e", ">")
        url = m.group(2).replace("\\", "")
        title_clean = re.sub(r"<[^>]+>", "", title)
        if url.startswith("http"):
            results.append(SearchResult(title=title_clean, url=url, snippet="", source="toutiao"))
            if len(results) >= max_results:
                break
    return results


async def _search_qwant(query: str, max_results: int = 6) -> list[SearchResult]:
    """Qwant API 方式"""
    api_url = f"https://api.qwant.com/v3/search/web?q={_quote(query)}&count={max_results}&locale=en_US"
    try:
        r = await _fetch_url(api_url)
        if r:
            data = json.loads(r)
            items = data.get("data", {}).get("result", {}).get("items", {}).get("mainline", [])
            results = []
            for item in items:
                for sub in item.get("items", []):
                    results.append(
                        SearchResult(
                            title=sub.get("title", ""),
                            url=sub.get("url", ""),
                            snippet=sub.get("desc", ""),
                            source="qwant",
                        )
                    )
            return results
    except Exception:
        pass
    return []


async def _search_prosearch(query: str, max_results: int = 10) -> list[SearchResult]:
    """腾讯元宝 ProSearch — 最稳定的中文/通用搜索通道。
    通过本地的 prosearch.cjs Node.js 脚本调用 Auth Gateway。
    """
    script_path = os.path.expanduser("~/.qclaw/skills/online-search/scripts/prosearch.cjs")
    if not os.path.exists(script_path):
        logger.warning("prosearch.cjs 不存在: %s", script_path)
        return []

    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            script_path,
            f"--keyword={query}",
            f"--cnt={min(max_results, 15)}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode != 0:
            logger.warning(
                "prosearch 失败 (exit=%d): %s", proc.returncode, (stderr or b"").decode("utf-8", errors="replace")[:100]
            )
            return []

        raw = stdout.decode("utf-8", errors="replace")
        # 解析 JSON 输出
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
                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        source="prosearch",
                    )
                )
                if len(results) >= max_results:
                    break

        logger.info("prosearch: %s → %d 条", query, len(results))
        return results

    except TimeoutError:
        logger.warning("prosearch 超时 (15s): %s", query)
        return []
    except Exception as e:
        logger.warning("prosearch 异常: %s", str(e)[:60])
        return []


# ──────────────── 缺口分析 ────────────────


def _analyze_gaps(query: str, results: list[SearchResult]) -> dict:
    """分析已收集信息中的缺口，生成扩展查询"""
    # 1. 文本汇总
    all_text = " ".join(f"{r.title} {r.snippet}" for r in results)

    # 2. 检测已覆盖角度
    covered_angles = set()
    for angle, keywords in ANGLE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in all_text.lower():
                covered_angles.add(angle)
                break

    # 3. 未覆盖角度
    all_angles = set(ANGLE_KEYWORDS.keys())
    uncovered = all_angles - covered_angles

    # 4. 检测语言覆盖
    has_zh = any(r.language == "zh" for r in results)
    has_en = any(r.language == "en" for r in results)

    # 5. 来源多样性
    sources_used = set(r.source for r in results)

    # 6. 生成扩展查询
    expansions = []
    for angle in uncovered:
        kw = ANGLE_KEYWORDS[angle][0]
        expansions.append(f"{query} {kw}")

    # 需要英文源
    if not has_en:
        expansions.append(query)  # 不加中文扩展，天然搜英文
    # 需要中文源
    if not has_zh:
        expansions.append(query)

    return {
        "covered_angles": sorted(covered_angles),
        "uncovered_angles": sorted(uncovered),
        "has_zh": has_zh,
        "has_en": has_en,
        "sources_used": sorted(sources_used),
        "coverage_rate": round(len(covered_angles) / len(all_angles) * 100, 1),
        "expansions": expansions[:6],  # 最多生成6个扩展查询
        "is_saturated": len(uncovered) <= 1 and has_zh and has_en,
    }


# ──────────────── 结果融合 ────────────────


def _fuse_results(results: list[SearchResult]) -> dict:
    """去重 + 按维度归类 + 排序"""
    seen = set()
    unique = []
    by_dimension = {}
    dimension_order = list(DIMENSIONS.keys())

    for r in results:
        # URL 去核去重（去跟踪参数）
        key = r.url.split("?")[0].split("#")[0]
        if key and key not in seen:
            seen.add(key)
            # 继承维度来源
            dim = "general"  # fallback
            for d in dimension_order:
                if r.source in DIMENSIONS[d]["engines"]:
                    dim = d
                    break
            r.dimension = dim
            unique.append(r)
            if dim not in by_dimension:
                by_dimension[dim] = []
            by_dimension[dim].append(r)

    # 按维度顺序、引擎权重排
    def sort_key(r):
        dim_idx = dimension_order.index(r.dimension) if r.dimension in dimension_order else 99
        return (dim_idx, -ENGINE_WEIGHTS.get(r.source, 0))

    unique.sort(key=sort_key)

    return {
        "total_raw": len(results),
        "total_unique": len(unique),
        "by_dimension": {
            d: [
                {"title": r.title, "url": r.url, "snippet": r.snippet[:200], "source": r.source, "lang": r.language}
                for r in items
            ]
            for d, items in by_dimension.items()
        },
        "dimension_summary": {d: len(items) for d, items in by_dimension.items()},
    }


# ──────────────── 蜂巢搜索主引擎 ────────────────

# 引擎路由表
ENGINE_ROUTER = {
    "prosearch": _search_prosearch,
    "bing": _search_bing,
    "duckduckgo": _search_ddg,
    "baidu": _search_baidu,
    "sogou": _search_sogou,
    "360": _search_360,
    "toutiao": _search_toutiao,
    "qwant": _search_qwant,
}


async def _execute_wave(wave_cfg: dict, query: str, expansions: list[str] = None) -> list[SearchResult]:
    """执行一个搜索波次"""
    queries = [query]
    if expansions:
        queries.extend(expansions[:3])  # 最多再加3个扩展

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

        # 并行执行
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
                logger.debug("波次引擎失败 %s/%s: %s", dim, engine, str(e)[:30])

    # 按引擎权重排序
    all_results.sort(key=lambda r: -ENGINE_WEIGHTS.get(r.source, 0))

    # 截断到 max_total
    max_total = wave_cfg.get("max_total", 100)
    return all_results[:max_total]


@tool()
async def honeycomb_search(query: str, depth: str = "normal") -> dict:
    """
    蜂巢搜索 — Bumblebee专属地毯式搜索。
    多维度、多波次、智能扩展，覆盖通用/中文/技术/社交/权威/长尾6个维度。
    支持深度: quick(1波)/normal(2波)/full(3波)。
    每波搜索后自动分析信息缺口，生成扩展查询补充搜索。
    """
    start = time.time()
    waves = WAVES.get(depth, WAVES["normal"])

    all_results = []
    wave_log = []
    expansions = []

    logger.info("蜂巢搜索开始: query=%s depth=%s", query, depth)

    for i, wave in enumerate(waves):
        wave_results = await _execute_wave(wave, query, expansions)
        all_results.extend(wave_results)

        # 缺口分析（第1波后和整个搜索完成后）
        if i == 0 or i == len(waves) - 1:
            gap = _analyze_gaps(query, all_results)
            expansions = gap["expansions"]

            wave_log.append(
                {
                    "wave": wave["name"],
                    "engine_count": len(set(r.source for r in wave_results)),
                    "result_count": len(wave_results),
                    "coverage": gap["coverage_rate"],
                    "is_saturated": gap["is_saturated"],
                    "uncovered": gap["uncovered_angles"],
                }
            )

            logger.info(
                "波次%d完成: %s, 覆盖率=%s%%, 饱和=%s", i + 1, wave["name"], gap["coverage_rate"], gap["is_saturated"]
            )

            # 如果信息已饱和（≥5角度覆盖 + 双语 + 多源），提前结束
            if gap["is_saturated"] and i < len(waves) - 1:
                logger.info("信息饱和，提前结束搜索")
                wave_log.append({"note": "信息饱和，提前终止后续波次"})
                break

    # 最终融合
    fused = _fuse_results(all_results)
    elapsed = round(time.time() - start, 2)

    return {
        "query": query,
        "depth": depth,
        "elapsed_s": elapsed,
        "waves": wave_log,
        "results": fused,
    }


# ──────────────── CLI 入口 ────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Universal 蜂巢搜索引擎")
    parser.add_argument("--query", "-q", required=True, help="搜索关键词")
    parser.add_argument("--depth", "-d", default="normal", choices=["quick", "normal", "full"])
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    result = asyncio.run(honeycomb_search(args.query, args.depth))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"🐝 蜂巢搜索：{result['query']}")
        print(f"深度: {result['depth']} | 耗时: {result['elapsed_s']}s")
        print(f"{'=' * 60}")

        for wave in result["waves"]:
            r = wave.get("result_count", 0)
            c = wave.get("coverage", 0)
            s = "✅ " if wave.get("is_saturated") else "🔄 "
            print(f"\n{s}波次「{wave.get('wave', '?')}」: {r}条, 覆盖{c}%, 缺口: {wave.get('uncovered', [])}")

        fused = result["results"]
        print(f"\n📊 总计: {fused['total_raw']}原始 → {fused['total_unique']}去重")

        for dim, items in sorted(fused["by_dimension"].items(), key=lambda x: -len(x[1])):
            print(f"\n  ── {dim} ({len(items)}条) ──")
            for item in items[:3]:
                print(f"    • {item['title'][:60]}")
                print(f"      {item['url'][:70]}")
