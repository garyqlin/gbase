# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/search.py

网络搜索工具：多引擎并行 + BeautifulSoup 解析 + 自动补 snippet + 内存缓存。
"""

import asyncio
import logging
import re
import urllib.parse

from bs4 import BeautifulSoup

from lib.fetcher import Fetcher
from lib.toolkit import tool

logger = logging.getLogger(__name__)

# ── 搜索引擎配置 ──


class SearchEngine:
    """搜索引擎描述。"""

    def __init__(self, name: str, lang: str, weight: float = 1.0):
        self.name = name
        self.lang = lang  # "zh" | "en" | "all"
        self.weight = weight  # 结果排序权重


ENGINES = {
    "bing": SearchEngine("Bing", "all", weight=1.5),
    "bing_cn": SearchEngine("Bing中文", "zh", weight=1.3),
    "duckduckgo": SearchEngine("DuckDuckGo", "en", weight=1.0),
    "ddg_lite": SearchEngine("DDG Lite", "en", weight=0.9),
    "quark": SearchEngine("夸克", "zh", weight=1.0),
    "swisscows": SearchEngine("Swisscows", "en", weight=0.7),
    "sogou": SearchEngine("搜狗", "zh", weight=1.4),
    "qwant": SearchEngine("Qwant", "all", weight=0.9),
    "startpage": SearchEngine("Startpage", "en", weight=0.8),
    "so": SearchEngine("360", "zh", weight=1.4),
    "baidu": SearchEngine("百度", "zh", weight=0.8),
}

# 中文查询默认引擎（so 在阿里云被 qcaptcha 风控，改用 sosou+qwant+baidu）
ZH_ENGINES = ["bing_cn", "sogou", "qwant", "duckduckgo"]
# 英文查询默认引擎
EN_ENGINES = ["bing", "ddg_lite", "qwant", "duckduckgo", "startpage"]

ENGINE_MAP: dict[str, callable] = {}


# ── 缓存 ──

_cache: dict[str, dict] = {}
"""内存缓存：query_hash → { data, ts }"""


def _is_chinese(query: str) -> bool:
    """判断查询是否以中文为主。"""
    cn = sum(1 for c in query if "\u4e00" <= c <= "\u9fff")
    return cn > len(query) * 0.3


def _query_key(query: str) -> str:
    q = query.lower().strip()
    for ch in "，。！？；：、''（）【】《》?.,!;:'\"()[]":
        q = q.replace(ch, " ")
    return " ".join(q.split())


# ── 通用 HTML 解析函数 ──


def _parse_common(
    soup: BeautifulSoup,
    result_selector: str,
    title_selector: str,
    url_attr: str = "href",
    _title_attr: str | None = None,
    snippet_selector: str | None = None,
    max_results: int = 5,
) -> list[dict]:
    """通用搜索引擎结果解析。

    用 BeautifulSoup 的 CSS 选择器提取结果列表。
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

        # 清理 URL
        if url.startswith("//"):
            url = "https:" + url

        entry = {"title": title[:120], "url": url, "snippet": ""}

        if snippet_selector:
            snip = item.select_one(snippet_selector)
            if snip:
                entry["snippet"] = snip.get_text(strip=True)[:400]

        results.append(entry)
    return results


# ── 引擎实现 ──


async def _bing(query: str, fetcher: Fetcher, market: str = "en-US") -> list[dict]:
    """Bing 搜索。"""
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
    """DuckDuckGo 零点击 API。"""
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
    """Swisscows 搜索。"""
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


async def _so_360(query: str, fetcher: Fetcher) -> list[dict]:
    # ---- 先试 JSON API ----
    json_url = "https://sug.so.360.cn/suggest"
    params = {"word": query, "encodein": "utf-8", "encodeout": "utf-8"}
    try:
        data = await fetcher.fetch_json(f"{json_url}?{urllib.parse.urlencode(params)}", timeout=5)
        if data and isinstance(data, dict):
            items = data.get("result", [])[:5]
            if items:
                return [{"title": item.get("word", query)[:120], "url": "", "snippet": ""} for item in items]
    except Exception:
        pass

    # ---- fallback: HTML 解析 ----
    url = f"https://www.so.com/s?q={urllib.parse.quote(query)}"
    html = await fetcher.fetch(url, timeout=10)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    results = []
    for item in soup.select("li.res-list")[:5]:
        # 标题
        a = item.select_one("h3 a, h3.res-title a, .res-title a")
        if not a:
            continue
        title = a.get_text(strip=True)[:120]
        href = a.get("href", "")
        if not href or not title:
            continue

        entry = {"title": title, "url": href, "snippet": ""}

        # 摘要：多种位置
        for sel in [".mh-news-desc", "p.res-desc", ".res-list-summary"]:
            el = item.select_one(sel)
            if el:
                txt = el.get_text(strip=True)[:600]
                if len(txt) > 15:
                    entry["snippet"] = txt
                    break

        results.append(entry)
    return results


async def _baidu(query: str, fetcher: Fetcher) -> list[dict]:
    """百度搜索移动端。"""
    url = f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}"
    html = await fetcher.fetch(url, timeout=10)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")

    results = []
    for item in soup.select(".result, .result-op")[:5]:
        title_el = item.select_one("h3 a, .t a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        url_val = title_el.get("href", "")
        if not title or not url_val:
            continue

        entry = {"title": title[:120], "url": url_val, "snippet": ""}

        # 百度摘要位置：span.content-right_XXX, div.c-abstract, span.c-gap-bottom
        snip = item.select_one("span.content-right_*, span.c-gap-bottom, div.c-abstract, .c-row .c-span-last")
        if snip:
            entry["snippet"] = snip.get_text(strip=True)[:400]

        # 再试 .c-abstract
        if not entry["snippet"]:
            snip2 = item.select_one(".c-abstract")
            if snip2:
                entry["snippet"] = snip2.get_text(strip=True)[:400]

        results.append(entry)

    return results


async def _sogou(query: str, fetcher: Fetcher) -> list[dict]:
    """搜狗搜索。"""
    url = f"https://www.sogou.com/web?query={urllib.parse.quote(query)}"
    html = await fetcher.fetch(url, timeout=10)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_common(
        soup,
        result_selector=".vrwrap, .rb, .vr-title, .vr5k-ctn",
        title_selector="h3 a, .vr-title a, .vr-tit a, .vr5k-title a",
        snippet_selector=".star-wiki, .str-text, .vr5k-summary, .str-info, .space-txt, p.str-kind",
        max_results=5,
    )


async def _ddg_lite(query: str, fetcher: Fetcher) -> list[dict]:
    """DuckDuckGo Lite 版（纯 HTML，无 JS 依赖）。"""
    url = f"https://lite.duckduckgo.com/lite?q={urllib.parse.quote(query)}"
    html = await fetcher.fetch(url, timeout=10)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    results = []
    # DDG Lite 表格布局：<tr class="result"><td class="result-snippet">...</td></tr>
    for row in soup.select("tr.result")[:5]:
        snippet_td = row.select_one("td.result-snippet")
        if snippet_td:
            a = snippet_td.select_one("a")
            if a:
                title = a.get_text(strip=True)[:120]
                href = a.get("href", "")
                # snippet 是 a 之后的文本（或相邻 td）
                snippet = ""
                # 尝试 .snippet 或文本节点
                frag = snippet_td.select_one(".snippet")
                if frag:
                    snippet = frag.get_text(strip=True)[:400]
                else:
                    # fallback: 提取 a 之外的文本
                    a_text = a.get_text() if a else ""
                    all_text = snippet_td.get_text()
                    extra = all_text.replace(a_text, "", 1).strip()
                    if extra:
                        snippet = extra[:400]
                results.append({"title": title, "url": href, "snippet": snippet})
    return results


async def _qwant(query: str, fetcher: Fetcher) -> list[dict]:
    """Qwant 搜索（无 API Key 的 HTML 版）。"""
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
    """Startpage 搜索（隐私搜索引擎，走 HTML）。"""
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


# ── 注册引擎映射 ──

ENGINE_MAP["bing"] = _bing
ENGINE_MAP["bing_cn"] = lambda q, f: _bing(q, f, "zh-CN")
ENGINE_MAP["duckduckgo"] = _ddg
ENGINE_MAP["ddg_lite"] = _ddg_lite
ENGINE_MAP["swisscows"] = _swisscows
ENGINE_MAP["so"] = _so_360
ENGINE_MAP["sogou"] = _sogou
ENGINE_MAP["qwant"] = _qwant
ENGINE_MAP["startpage"] = _startpage
ENGINE_MAP["baidu"] = _baidu


# ── 并行搜索 ──


async def _parallel_search(engines: list[str], query: str, fetcher: Fetcher) -> list[dict]:
    """并行调用多个搜索引擎，返回合并去重结果。"""
    tasks = {}
    for name in engines:
        func = ENGINE_MAP.get(name)
        if func:
            tasks[name] = asyncio.create_task(func(query, fetcher))
        else:
            logger.warning("未知引擎: %s", name)

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
            logger.debug("引擎 %s 超时", name)
        except Exception as e:
            logger.debug("引擎 %s 失败: %s", name, e)

    all_results.sort(key=lambda r: r.get("_weight", 0), reverse=True)
    return all_results


def _get_expansion_queries(query: str) -> list[str]:
    """为中文查询生成同音/近义扩词版本。
    当搜索结果过少时自动换不同表达再试。
    """
    expansions = []
    # 同音字替换常用姓氏/名字
    homophones = {
        "周": ["周", "邹", "钟"],
        "张": ["张", "章", "彰"],
        "李": ["李", "黎", "理", "里"],
        "王": ["王", "汪", "万"],
        "陈": ["陈", "程", "晨", "辰"],
        "林": ["林", "凌", "玲", "琳"],
        "吴": ["吴", "武", "伍", "巫"],
        "刘": ["刘", "柳", "留"],
        "黄": ["黄", "皇", "凰"],
        "赵": ["赵", "照", "召"],
        "杨": ["杨", "洋", "阳"],
        "朱": ["朱", "祝", "诸"],
    }
    # 尝试每个字替换
    for i, ch in enumerate(query):
        if ch in homophones:
            for alt in homophones[ch]:
                if alt != ch:
                    expansions.append(query[:i] + alt + query[i + 1 :])
    # 加"加"字试拼写变体（人名常丢失一个字）
    # 不加多余字符，只通过替换扩展
    return expansions


async def _fetch_body(url: str, fetcher, max_chars: int = 2000) -> str:
    """通用正文提取：抓取页面 HTML 去标签后返回纯文本。
    跳过百科/词典等（snippet 已经够用）。
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
    """搜索互联网，返回相关结果摘要。

    自动选择中文/英文引擎。支持指定引擎列表。

    Args:
        query: 搜索关键词
        engines: 可选，指定引擎名称列表，如 ["bing", "baidu"]。默认按语言自动选择。
    """
    # ── 隧道优先：先试 SSH 反向隧道到本地 ProSearch ──
    try:
        from tools.search_tunnel import search_via_tunnel

        tunnel_results = await search_via_tunnel(query, 8)
        if tunnel_results:
            logger.info("隧道搜索命中: %s (%d 条)", query, len(tunnel_results))
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
                "note": "来自 ProSearch (SSH 隧道)",
            }
            # 缓存
            _cache[_query_key(query)] = {"data": data, "ts": 0}
            return data
    except Exception as e:
        logger.warning("隧道搜索失败 (%s)，回退自爬引擎", str(e)[:60])

    # ── 缓存检查 ──
    cache_key = _query_key(query)
    cached = _cache.get(cache_key)
    if cached:
        logger.info("搜索缓存命中: %s", query)
        return cached["data"]

    # 兼容 LLM 传字符串 → 列表
    if isinstance(engines, str):
        engines = [e.strip() for e in re.split(r"[,\s]+", engines) if e.strip()]

    # 过滤有效引擎
    engine_list = engines
    valid_names = set(ENGINES.keys())
    if engine_list:
        engine_list = [e for e in engine_list if e in valid_names]
        if not engine_list:
            logger.warning("指定引擎全部无效，回退自动选择")

    if not engine_list:
        engine_list = ZH_ENGINES if _is_chinese(query) else EN_ENGINES

    logger.info("搜索: %s -> 引擎: %s", query, " + ".join(engine_list))

    fetcher = Fetcher()
    try:
        results = await _parallel_search(engine_list, query, fetcher)

        # ── 结果不足自动扩词 ──
        # 如果搜索结果 < 3，用同音字替换再搜一次
        if len(results) < 3 and len(query) >= 2:
            expansions = _get_expansion_queries(query)
            if expansions:
                logger.info("结果不足 (%d)，自动扩词: %s", len(results), " / ".join(expansions[:2]))
                for eq in expansions[:2]:
                    extra = await _parallel_search(ZH_ENGINES, eq, fetcher)
                    existing_urls = {r.get("url", "") for r in results}
                    for r in extra:
                        if r.get("url", "") not in existing_urls:
                            results.append(r)
                            existing_urls.add(r.get("url", ""))
                logger.info("扩词后结果数: %d", len(results))

        # ── 桥接回退：自爬无效时调本地 search_bridge ──
        if len(results) < 3:
            try:
                import json as _json
                import urllib.request as _ur

                payload = _json.dumps({"query": query, "count": 8}).encode()
                body = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: (
                        _ur.urlopen(
                            _ur.Request(
                                "http://127.0.0.1:8430/search",
                                data=payload,
                                headers={"Content-Type": "application/json"},
                            ),
                            timeout=10,
                        )
                        .read()
                        .decode("utf-8", errors="replace")
                    ),
                )
                if body:
                    bdata = _json.loads(body)
                    bresults = bdata.get("results", [])
                    if bresults:
                        logger.info("桥回退命中: %s (%d 条)", query, len(bresults))
                        existing_urls = {r.get("url", "") for r in results}
                        for r in bresults:
                            if r.get("url", "") not in existing_urls:
                                results.append(r)
                                existing_urls.add(r.get("url", ""))
            except Exception as e:
                logger.warning("桥回退失败: %s", str(e)[:60])

        # 格式化输出
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

        # 自动 fetch 前2个结果页的正文，让 LLM 拿到完整内容
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
            "note": f"来自 {len(engine_list)} 个引擎并行搜索" if len(engine_list) > 1 else "",
        }

        # 缓存
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
    """获取网页内容（文本摘要版本）。"""
    fetcher = Fetcher(timeout=20)
    try:
        text = await fetcher.fetch(url, timeout=20)
        if text is None:
            return {"error": "页面读取失败", "url": url}

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
    """搜索自己的经验库，查找已积累的知识。

    当需要回忆自己之前做过什么、学到了什么、或类似问题如何处理时使用。

    Args:
        question: 要搜索的经验问题
    """
    from lib import toolkit as tk

    ee = tk.get_global("experience_engine")
    if not ee:
        return {"found": False, "results": [], "note": "经验引擎未就绪"}

    try:
        memory = await ee.search(question)
        if memory and len(memory) > 0:
            return {"found": True, "count": len(memory), "results": memory}
        return {"found": False, "results": [], "note": "经验库中暂未找到相关记录"}
    except Exception as e:
        logger.warning("search_self 失败: %s", e)
        return {"found": False, "results": [], "note": f"查询异常: {e}"}


# ── single-pass search（供 kernel 内部调用）──


async def search_main(query: str) -> dict:
    """kernel 内部专用：一次搜索遍历引擎 + 自动提取全文，LLM 不参与决策。

    返回格式:
    {
        "query": str,
        "digest": str,   # 中英文搜索结果 + 全文内容包
        "sources": [],   # 原始链接
    }
    """
    logger.info("search_main: %s", query)
    _is_chinese(query)

    # 中英文各一批引擎
    cn_engines = ZH_ENGINES  # bing_cn + so + baidu
    en_engines = ["bing"]

    all_engines = cn_engines + [e for e in en_engines if e not in cn_engines]

    fetcher = Fetcher()
    try:
        results = await _parallel_search(all_engines, query, fetcher)
        if not results:
            return {"query": query, "digest": "", "sources": []}

        # 收集摘要（每个引擎取 top 3）
        from collections import OrderedDict

        extra_parts = []
        by_engine = OrderedDict()
        for r in results:
            eng = r.get("_engine", "?")
            if eng not in by_engine:
                by_engine[eng] = []
            if len(by_engine[eng]) < 3:
                by_engine[eng].append(r)
        for _eng, items in by_engine.items():
            for r in items:
                s = r.get("snippet", "") or ""
                if s and len(s) > 10:
                    extra_parts.append(f"• {r.get('title', '?')} — {s[:300]}")

        # 正文填充
        content_parts = []

        # 1) 360 新闻聚合页
        clean_q = re.sub(r"[别请帮忙好吗谢谢拜托可以吗大概多说点详细点搜]", "", query)[:30]
        news_url = f"https://news.so.com/ns?q={urllib.parse.quote(clean_q)}"
        body = await _fetch_body(news_url, fetcher)
        if body and "未找到" not in body and "建议您去" not in body:
            content_parts.append(f"📰 360资讯聚合\n{body}")

        # 2) 搜索结果中取可 fetch 的正文
        for r in results[:12]:
            url = r.get("url", "")
            if not url or "baidu.com" in url or "sogou.com" in url or "qcaptcha" in url:
                continue
            if any(d in url for d in ["baike.baidu", "zdic.net", "wenku.so"]):
                continue
            body = await _fetch_body(url, fetcher)
            if body:
                content_parts.append(f"📄 {r.get('title', '?')}\n{body}")
                break

        # 拼装 digest
        parts = []
        if extra_parts:
            parts.append("【搜索结果摘要】")
            parts.extend(extra_parts[:5])
        if content_parts:
            parts.append("\n【详细内容】")
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
