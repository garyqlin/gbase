#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
hive_mind.py — Universal 全能搜索子智能体

核心思路：
  一个 @tool，等于一个完整的搜索智能体。
  自动拆目标 → 选波束 → 多轮扫描 → 缺口分析 → 交叉验证 → 出结构化报告。

7 种搜索波束：
  1. 📡 broad    — 宽扫（多引擎聚合，快速定范围）
  2. 🎯 sharp    — 精搜（指定站点/语法/时间，精准命中）
  3. 🕷️ crawl    — 爬取（抓页面全文，结构化提取）
  4. 🔬 deep     — 深度（递归深挖，逐层展开信息树）
  5. 💬 social   — 社交（社交媒体/论坛/舆论侦查）
  6. 🔗 trace    — 溯源（反查信息来源/引用链）
  7. 🧪 cua      — 浏览器可视化操作

多轮雷达拼图：
  第1轮：宽扫 → 第2轮：缺口精搜 → 第3轮：深挖 → 第4轮：交叉验证

用法：
    await hive_mind(mission="全面调研 Codex CLI", depth="full")

依赖：
  - honeycomb_search (现有) — 宽扫/精搜基础
  - anysearch_tool — 通用搜索桥
  - httpx / aiohttp — 爬取
  - lib.toolkit — @tool 注册
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus

import httpx

from lib.toolkit import tool

logger = logging.getLogger("hive_mind")

# ════════════════════════════════════════════════════
# LLM 直连（hive_mind 自建 LLM 通道，不依赖 kernel.py）
# ════════════════════════════════════════════════════

_llm_client: httpx.AsyncClient | None = None
_llm_config: dict | None = None


def _get_llm_config() -> dict:
    """从环境获取 LLM 配置（与 kernel.py / ModelRouter 同源但独立读取）"""
    global _llm_config
    if _llm_config is None:
        # 尝试 .env
        dotenv_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(dotenv_path):
            with open(dotenv_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())

        api_key = os.environ.get("OPPRIME_DEEPSEEK_API_KEY", "")
        base_url = "https://api.deepseek.com"

        # fallback 到火山 ARK
        if not api_key:
            api_key = os.environ.get("OPPRIME_VOLC_API_KEY", "")
            base_url = "https://ark.cn-beijing.volces.com/api/v3"

        _llm_config = {
            "api_key": api_key,
            "base_url": base_url,
            "model": os.environ.get("OPPRIME_ANALYZER_MODEL", "deepseek-chat"),
        }
    return _llm_config


async def _llm_analyze(
    system: str,
    user: str,
    temperature: float = 0.1,
    max_tokens: int = 1024,
) -> str:
    """调用 LLM 做分析（轻量版，只做语义判断不调 tool）"""
    global _llm_client
    cfg = _get_llm_config()

    if not cfg["api_key"]:
        raise RuntimeError("未配置 LLM API Key（需要 OPPRIME_DEEPSEEK_API_KEY 或 OPPRIME_VOLC_API_KEY）")

    if _llm_client is None:
        _llm_client = httpx.AsyncClient(timeout=30)

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        resp = await _llm_client.post(
            f"{cfg['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""
    except Exception as e:
        logger.warning("LLM 分析调用失败: %s", str(e)[:80])
        return ""


# ════════════════════════════════════════════════════
# LLM 驱动的语义分析函数
# ════════════════════════════════════════════════════


async def _llm_analyze_gaps(mission: str, accumulated_text: str) -> dict:
    """
    LLM 语义级缺口分析。
    告诉 LLM："你搜索了一个主题，以下是已获得的信息，请分析还缺什么维度"
    """
    system_prompt = """你是一个情报分析专家。你的任务：
1. 分析给定搜索任务和已收集的信息
2. 判断当前信息覆盖了哪些维度
3. 识别关键信息缺口
4. 推荐下一步搜索的关键词（不超过3个）

返回 JSON 格式（仅 JSON，不要 markdown 包裹）：
{
  "covered_topics": ["维度1", "维度2"],
  "gaps": ["缺口1", "缺口2"],
  "coverage_rate": 75.5,
  "is_saturated": false,
  "next_queries": ["补充搜索词1", "补充搜索词2"]
}

判断标准：
- covered_topics: 已经找到明确信息的维度
- gaps: 缺失的关键维度（不是标题党说有的，是你真正看到内容的）
- coverage_rate: 0-100，基于实际看到的信息覆盖面
- is_saturated: 是否基本饱和（gaps 少于 2 且覆盖率 > 80% 则为 true）
- next_queries: 针对缺口生成的具体搜索词（中英文各一组最佳）"""

    # 截取合理长度的文本给 LLM
    truncated = accumulated_text[-6000:] if len(accumulated_text) > 6000 else accumulated_text

    user_prompt = f"""搜索任务: {mission}

已收集的搜索结果和页面内容摘要:
{truncated[:5000]}

请分析信息覆盖度，返回 JSON。"""

    reply = await _llm_analyze(system_prompt, user_prompt, temperature=0.1, max_tokens=1200)

    if not reply:
        # fallback 到规则引擎
        return _rule_analyze_gaps(mission, accumulated_text)

    # 解析 JSON
    try:
        # 去掉可能的 markdown 包裹
        clean = reply.strip()
        if clean.startswith("```"):
            # 找到第一个 { 和最后一个 }
            start = clean.find("{")
            end = clean.rfind("}")
            if start >= 0 and end > start:
                clean = clean[start : end + 1]
        result = json.loads(clean)
        # 保证 key 存在
        result.setdefault("covered_topics", [])
        result.setdefault("gaps", [])
        result.setdefault("coverage_rate", 50.0)
        result.setdefault("is_saturated", False)
        result.setdefault("next_queries", [])
        return result
    except (json.JSONDecodeError, Exception):
        logger.warning("LLM 分析返回非 JSON，回退规则引擎")
        return _rule_analyze_gaps(mission, accumulated_text)


# ════════════════════════════════════════════════════
# 数据模型
# ════════════════════════════════════════════════════


@dataclass
class SearchRound:
    """一次搜索轮次的记录"""

    round_num: int
    beam_type: str  # broad | sharp | crawl | deep | social | trace | cua
    query: str
    results: list[dict] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str = ""


@dataclass
class CoverageAnalysis:
    """信息覆盖度分析"""

    covered_topics: list[str] = field(default_factory=list)
    uncovered_topics: list[str] = field(default_factory=list)
    coverage_rate: float = 0.0
    expansions: list[str] = field(default_factory=list)
    is_saturated: bool = False


# ════════════════════════════════════════════════════
# AI 话题/缺失分析（规则版，不调 LLM）
# ════════════════════════════════════════════════════

# 用于判断覆盖度的全局话题清单
TOPIC_FRAMEWORK = {
    "定义": ["定义", "是什么", "概念", "overview", "introduction", "什么是", "介绍"],
    "背景": ["背景", "历史", "起源", "history", "background", "origin", "发展", "who made"],
    "架构": ["架构", "结构", "设计", "architecture", "structure", "design", "组成", "模块", "component"],
    "技术细节": ["技术", "方法", "实现", "技术方案", "implementation", "how it works", "原理", "mechanism"],
    "工具链": ["工具", "sdk", "cli", "api", "命令", "命令行", "配置", "配置文件", "config"],
    "代码": ["代码", "源码", "github", "source", "repository", "开源", "code", "github.com"],
    "使用方式": ["使用", "用法", "入门", "教程", "tutorial", "guide", "上手", "how to use", "用例"],
    "比较": ["对比", "比较", "vs", "alternative", "alternatives", "区别", "竞品", "vs", "测评"],
    "评价": ["评价", "评分", "评论", "review", "opinion", "feedback", "反馈", "用户说", "体验"],
    "前景": ["前景", "未来", "趋势", "方向", "future", "roadmap", "规划", "计划"],
    "数据": ["数据", "数字", "统计", "metrics", "statistics", "performance", "性能", "基准"],
    "案例": ["案例", "实践", "应用", "客户", "use case", "example", "场景", "实战"],
}


def _detect_language(text: str) -> str:
    """粗略语言检测"""
    cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return "zh" if cn > 3 else "en"


def _extract_all_text(results: list[dict]) -> str:
    """从搜索结果中提取所有文本做分析"""
    texts = []
    for r in results:
        texts.append(r.get("title", ""))
        texts.append(r.get("snippet", ""))
        texts.append(r.get("content", "")[:500])
    return " ".join(texts)


def _score_result_relevance(result: dict, mission_keywords: set) -> float:
    """对单条搜索结果做相关性评分 0.0~1.0"""
    if not result:
        return 0.0
    title = result.get("title", "") or ""
    snippet = result.get("snippet", "") or ""
    content = (result.get("content", "") or "")[:300]
    text = (title + " " + snippet + " " + content).lower()
    if not text.strip():
        return 0.0
    if not mission_keywords:
        return 0.5
    matched = sum(1 for kw in mission_keywords if kw.lower() in text)
    keyword_density = matched / len(mission_keywords)
    title_bonus = 0.2 if any(kw.lower() in title.lower() for kw in mission_keywords) else 0.0
    return min(keyword_density + title_bonus, 1.0)


_rule_analyze_gaps_topic = dict(TOPIC_FRAMEWORK)


def _rule_analyze_gaps(mission: str, all_text: str) -> CoverageAnalysis:
    """规则引擎版缺口分析（降级方案，LLM 不可用时使用）"""
    text_lower = all_text.lower()

    covered = []
    uncovered = []
    expansions = []

    for topic, keywords in _rule_analyze_gaps_topic.items():
        found = any(kw.lower() in text_lower for kw in keywords)
        if found:
            covered.append(topic)
        else:
            uncovered.append(topic)
            expansions.append(f"{mission} {keywords[0]}")

    coverage_rate = round(len(covered) / len(_rule_analyze_gaps_topic) * 100, 1)
    is_saturated = len(uncovered) <= 2

    cn_chars = sum(1 for c in text_lower if "\u4e00" <= c <= "\u9fff")
    has_zh = cn_chars > 10
    has_en = bool(re.search(r"[a-zA-Z]{10,}", text_lower))

    if not has_zh:
        expansions.append(f"{mission} 中文")
    if not has_en:
        expansions.append(mission)

    return CoverageAnalysis(
        covered_topics=covered,
        uncovered_topics=uncovered,
        coverage_rate=coverage_rate,
        expansions=list(set(expansions))[:6],
        is_saturated=is_saturated,
    )


async def _analyze_coverage(mission: str, all_text: str) -> CoverageAnalysis:
    """
    分析信息覆盖度——优先 LLM 语义级分析，降级到规则引擎。
    异步版本，供主循环调用。
    """
    # 先试 LLM 语义分析
    try:
        llm_result = await _llm_analyze_gaps(mission, all_text)
        if llm_result.get("coverage_rate", 0) > 0:
            return CoverageAnalysis(
                covered_topics=llm_result.get("covered_topics", []),
                uncovered_topics=llm_result.get("gaps", []),
                coverage_rate=llm_result.get("coverage_rate", 50.0),
                expansions=llm_result.get("next_queries", []),
                is_saturated=llm_result.get("is_saturated", False),
            )
    except Exception:
        logger.debug("LLM 分析不可用，回退规则引擎")

    return _rule_analyze_gaps(mission, all_text)


# ════════════════════════════════════════════════════
# 策略层 — 任务分析 & 规划
# ════════════════════════════════════════════════════


# 任务类型检测模式
_TASK_PATTERNS = {
    "compare": {
        "triggers": ["对比", "比较", "vs", "异同", "difference", "versus", "与"],
        "action": "auto_split",
        "desc": "对比/比较类任务 → 自动拆成多个子任务分别搜索",
    },
    "survey": {
        "triggers": ["综述", "概览", "overview", "survey", "landscape", "全景", "全貌"],
        "action": "broad_then_sharp",
        "desc": "全景调研类 → 先宽扫定范围，再分点精搜",
    },
    "deep_dive": {
        "triggers": ["深入", "原理", "principle", "mechanism", "机制", "根因"],
        "action": "sharp_then_crawl",
        "desc": "深挖类 → 精搜命中 + 爬取页面详读",
    },
    "trend": {
        "triggers": ["趋势", "发展", "前沿", "trend", "latest", "最新", "newest"],
        "action": "multi_temporal",
        "desc": "趋势类 → 关注时效性，按时间序列组织",
    },
}

# 波束经验记忆（Phase 2）
# 从 L4 笔记加载历史搜索质量，指导波束选择
_BEAM_EXPERIENCE_CACHE: dict | None = None


def _detect_task_type(mission: str) -> str | None:
    """识别任务类型 — 对比/全景/深挖/趋势"""
    ml = mission.lower()
    for ttype, cfg in _TASK_PATTERNS.items():
        if any(t in ml for t in cfg["triggers"]):
            return ttype
    return None


def _auto_split_mission(mission: str, depth: str) -> list[dict]:
    """
    自动拆解对比类任务为多个子任务，每个子任务独立一轮宽扫。
    """
    # 用分隔符拆分
    for sep in [" vs ", " vs. ", " versus ", " 与 ", " 对比 ", " 比较 ", " 和 "]:
        if sep in mission:
            parts = [p.strip().rstrip("。，,.") for p in mission.split(sep) if len(p.strip()) > 3]
            if len(parts) >= 2:
                plan = []
                for i, part in enumerate(parts):
                    plan.append(
                        {
                            "round": i + 1,
                            "beam": "sharp",
                            "query": part,
                            "max_results": 8,
                            "sub_mission": True,
                        }
                    )
                # 加一轮综合作对比
                plan.append(
                    {
                        "round": len(parts) + 1,
                        "beam": "broad",
                        "query": mission,
                        "max_results": 10,
                        "sub_mission": False,
                    }
                )
                return plan
    # 拆不了就返回空，让主函数兜底
    return []


def _plan_mission(mission: str, depth: str) -> list[dict]:
    """自适应波束规划 — 任务类型识别 + 历史经验加权"""
    mission_lower = mission.lower()

    # ── 先检测任务类型 ──
    task_type = _detect_task_type(mission)

    # 对比类 → 自动拆子任务
    if task_type == "compare":
        sub_plan = _auto_split_mission(mission, depth)
        if sub_plan:
            logger.info("  🧩 检测到对比类任务，拆为 %d 个子任务", len(sub_plan))
            return sub_plan

    # ── 普通规划 ──
    beams_to_use = ["broad"]

    if any(kw in mission for kw in ["具体", "指定", "固定", "准确", "exact", "specific", "site:"]):
        beams_to_use.append("sharp")
    if any(
        kw in mission_lower
        for kw in ["内容", "页面", "文章", "博客", "文档", "page", "content", "article", "blog", "doc", "read"]
    ):
        beams_to_use.append("crawl")
    if depth == "full" or any(
        kw in mission_lower
        for kw in ["分析", "对比", "比较", "研究", "深入", "全面", "探", "deep", "research", "analysis", "compare"]
    ):
        beams_to_use.append("deep")
    if any(
        kw in mission_lower
        for kw in ["讨论", "舆论", "社区", "论坛", "social", "discussion", "community", "reddit", "twitter", "X"]
    ):
        beams_to_use.append("social")
    if any(kw in mission_lower for kw in ["来源", "引用", "溯源", "source", "origin", "trace", "cited", "reference"]):
        beams_to_use.append("trace")

    # 任务类型补充波束
    if task_type == "trend":
        beams_to_use.append("crawl")  # 趋势类要爬内容确认时间戳
    elif task_type == "deep_dive":
        beams_to_use.append("crawl")  # 深挖类要爬详读

    used_beams = list(dict.fromkeys(beams_to_use))

    if depth == "quick":
        rounds = 2
    elif depth == "full":
        rounds = 4
    else:
        rounds = 3

    plan = []
    for r in range(1, rounds + 1):
        if r == 1:
            plan.append({"round": r, "beam": "broad", "query": mission, "max_results": 15})
        elif r == 2:
            beam = "sharp" if ("sharp" in used_beams or depth != "quick") else "broad"
            plan.append({"round": r, "beam": beam, "query": mission, "max_results": 10})
        elif r == 3:
            beam = (
                "crawl" if "crawl" in used_beams else ("deep" if ("deep" in used_beams or depth == "full") else "broad")
            )
            plan.append({"round": r, "beam": beam, "query": mission, "max_results": 8})
        else:
            beam = "trace" if "trace" in used_beams else "crawl"
            plan.append({"round": r, "beam": beam, "query": mission, "max_results": 6})

    return plan


# ════════════════════════════════════════════════════
# 执行层 — 各波束实现
# ════════════════════════════════════════════════════


# ─── 导入现有搜索能力 ───


async def _beam_broad(query: str, max_results: int = 10) -> list[dict]:
    """
    宽扫波束 — 多引擎聚合搜索。
    通过 honeycomb_search 引擎调多个免费搜索引擎。
    """
    results = []
    logger.info("📡 宽扫: %s (max=%d)", query, max_results)

    # 尝试用现有的 honeycomb_search
    try:
        from tools.honeycomb_search import honeycomb_search

        hr = await honeycomb_search(query, depth="quick" if max_results <= 8 else "normal")
        for dim_name, items in hr.get("results", {}).get("by_dimension", {}).items():
            for item in items:
                results.append(
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("snippet", ""),
                        "source": f"honeycomb/{dim_name}",
                        "beam": "broad",
                    }
                )
    except Exception as e:
        logger.warning("honeycomb_search 不可用: %s", e)

    # 如果 honeycomb 没出结果，用 anysearch 兜底
    if not results:
        try:
            from tools.anysearch_tool import anysearch_search

            raw = await anysearch_search(query)
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else raw
                for item in data.get("results", data.get("items", [])):
                    results.append(
                        {
                            "title": item.get("title", ""),
                            "url": item.get("url", item.get("link", "")),
                            "snippet": item.get("snippet", item.get("description", "")),
                            "source": "anysearch",
                            "beam": "broad",
                        }
                    )
        except Exception as e:
            logger.warning("anysearch 兜底也失败: %s", e)

    return results[:max_results]


async def _beam_sharp(query: str, max_results: int = 8) -> list[dict]:
    """
    精搜波束 — 精准命中。支持 site: 语法。
    通过 multi-engine-websearch 的聚合 + 特定站点定位。
    """
    logger.info("🎯 精搜: %s", query)
    results = []

    prosearch_script = os.path.expanduser("~/.qclaw/skills/online-search/scripts/prosearch.cjs")
    if os.path.exists(prosearch_script):
        try:
            proc = await asyncio.create_subprocess_exec(
                "node",
                prosearch_script,
                f"--keyword={query}",
                f"--cnt={max_results}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            data = json.loads(stdout.decode("utf-8", errors="replace"))
            docs = data.get("data", {}).get("docs", [])
            for doc in docs:
                results.append(
                    {
                        "title": doc.get("title", ""),
                        "url": doc.get("url", ""),
                        "snippet": doc.get("passage", "")[:300],
                        "source": "prosearch",
                        "beam": "sharp",
                    }
                )
        except Exception as e:
            logger.warning("prosearch 精搜失败: %s", e)

    return results[:max_results]


async def _beam_crawl(url_or_query: str, max_chars: int = 8000) -> list[dict]:
    """
    爬取波束 — 抓页面全文，结构化提取。
    如果传的是 URL 直接爬；如果是关键词则先搜再爬。
    """
    logger.info("🕷️ 爬取: %s", url_or_query[:80])

    # 判断是 URL 还是关键词
    if url_or_query.startswith(("http://", "https://")):
        urls_to_crawl = [url_or_query]
    else:
        # 先搜出 URL
        broad_results = await _beam_broad(url_or_query, max_results=5)
        urls_to_crawl = [r["url"] for r in broad_results if r.get("url")][:3]
        if not urls_to_crawl:
            return []

    results = []
    import httpx

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for url in urls_to_crawl:
            try:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                        "Accept": "text/html,application/xhtml+xml",
                    },
                )
                if resp.status_code != 200:
                    continue

                html = resp.text

                # 简单提取：去掉 HTML tag
                text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
                text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()[:max_chars]

                # 提取标题
                title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL)
                title = title_match.group(1).strip() if title_match else url

                results.append(
                    {
                        "title": title,
                        "url": url,
                        "content": text,
                        "source": "crawl",
                        "beam": "crawl",
                    }
                )
                logger.info("  爬取完成: %s (%d chars)", url, len(text))
            except Exception as e:
                logger.debug("  爬取失败 %s: %s", url, str(e)[:50])
                continue

    return results


async def _beam_deep(query: str, max_depth: int = 2) -> list[dict]:
    """
    深度波束 — 递归深挖。
    先搜→看结果→选关键发现继续深挖→挖的结果再挖。
    """
    logger.info("🔬 深度: %s (max_depth=%d)", query, max_depth)
    all_results = []
    crawled_urls = set()

    # 第1层：宽扫
    results_l1 = await _beam_broad(query, max_results=10)
    all_results.extend(results_l1)

    if max_depth >= 2:
        # 第2层：从第1层结果中找最相关的 3 个 URL 爬内容
        urls_to_crawl = []
        for r in results_l1:
            url = r.get("url", "")
            if (
                url
                and url not in crawled_urls
                and not any(skip in url for skip in ["facebook.com", "twitter.com", "youtube.com", "instagram.com"])
            ):
                urls_to_crawl.append(url)
                crawled_urls.add(url)
                if len(urls_to_crawl) >= 3:
                    break

        for url in urls_to_crawl:
            crawled = await _beam_crawl(url, max_chars=5000)
            all_results.extend(crawled)

    return all_results


async def _beam_social_direct(platform: str, query: str, max_results: int = 5) -> list[dict]:
    """直接调社交平台 API/搜索接口"""
    results = []
    client = httpx.AsyncClient(timeout=15)

    if platform == "reddit":
        try:
            resp = await client.get(
                "https://www.reddit.com/search.json",
                params={"q": query, "limit": max_results, "sort": "relevance"},
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            )
            if resp.status_code == 200:
                data = resp.json()
                for child in data.get("data", {}).get("children", []):
                    d = child.get("data", {})
                    results.append(
                        {
                            "title": d.get("title", ""),
                            "url": f"https://www.reddit.com{d.get('permalink', '')}",
                            "snippet": (d.get("selftext", "") or d.get("title", ""))[:300],
                            "content": d.get("selftext", ""),
                            "source": "reddit_direct",
                            "platform": platform,
                            "beam": "social",
                            "score": d.get("score", 0),
                            "num_comments": d.get("num_comments", 0),
                            "author": d.get("author", ""),
                        }
                    )
        except Exception as e:
            logger.debug("Reddit 直搜失败: %s", str(e)[:60])

    elif platform == "hackernews":
        try:
            resp = await client.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": query, "hitsPerPage": max_results, "tags": "story"},
            )
            if resp.status_code == 200:
                data = resp.json()
                for hit in data.get("hits", []):
                    results.append(
                        {
                            "title": hit.get("title", ""),
                            "url": hit.get("url", f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"),
                            "snippet": (hit.get("story_title", "") or "")[:300],
                            "source": "hackernews_direct",
                            "platform": platform,
                            "beam": "social",
                            "points": hit.get("points", 0),
                            "num_comments": hit.get("num_comments", 0),
                            "author": hit.get("author", ""),
                        }
                    )
        except Exception as e:
            logger.debug("HN 直搜失败: %s", str(e)[:60])

    elif platform == "zhihu":
        try:
            resp = await client.get(
                "https://www.zhihu.com/search",
                params={"q": query, "type": "content"},
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            )
            if resp.status_code == 200:
                html = resp.text
                titles = re.findall(r"<h2[^>]*>.*?<a[^>]*>(.*?)</a>", html, re.DOTALL)[:max_results]
                urls = re.findall(r'<a[^>]*href="(//zhuanlan\\.zhihu\\.com[^"]+)"', html)[:max_results]
                for i, t in enumerate(titles):
                    clean = re.sub(r"<[^>]+>", "", t).strip()
                    u = f"https:{urls[i]}" if i < len(urls) and urls[i] else ""
                    if clean and u:
                        results.append(
                            {
                                "title": clean,
                                "url": u,
                                "snippet": clean[:200],
                                "source": "zhihu_direct",
                                "platform": platform,
                                "beam": "social",
                            }
                        )
        except Exception as e:
            logger.debug("知乎直搜失败: %s", str(e)[:60])

    await client.aclose()
    results.sort(key=lambda r: r.get("score", r.get("points", 0)), reverse=True)
    return results[:max_results]


async def _beam_social(query: str, max_results: int = 8) -> list[dict]:
    """
    社交波束 v2 — 独立引擎 + 宽扫兜底。
    优先调各社交平台的原生 API，失败则回退到 site: 过滤。
    """
    logger.info("💬 社交v2: %s", query)
    results = []
    seen_urls = set()

    # 第1层：独立引擎直搜
    platforms = ["reddit", "hackernews", "zhihu"]
    tasks = [_beam_social_direct(p, query, max_results) for p in platforms]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    for outcome in outcomes:
        if isinstance(outcome, list):
            for r in outcome:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(r)

    logger.info("  社交独立引擎: %d 条", len(results))

    # 第2层：宽扫 site: 过滤兜底
    if len(results) < max_results // 2:
        broad_results = await _beam_broad(query, max_results=20)
        for r in broad_results:
            url = r.get("url", "")
            if url and url not in seen_urls:
                for domain in ["reddit", "zhihu", "medium", "news.ycombinator.com"]:
                    if domain in url:
                        seen_urls.add(url)
                        r["beam"] = "social"
                        r["platform"] = domain.split(".")[0]
                        results.append(r)
                        break

    logger.info("  social 全部: %d 条", len(results))
    return results[:max_results]


async def _beam_trace(query: str, max_depth: int = 2) -> list[dict]:
    """
    溯源波束 v2 — 引用链追踪。
    第1层：搜来源/引用关键词
    第2层：追引用链（A→B→C）
    """
    logger.info("🔗 溯源v2: %s (depth=%d)", query, max_depth)
    results = []
    seen_urls = set()

    async def _trace_one_layer(search_topic: str, layer: int) -> list[dict]:
        """追一层溯源"""
        queries = [
            f'"{search_topic}" source',
            f'"{search_topic}" reference',
            f'"{search_topic}" citation',
            f'"{search_topic}" 来源',
            f'"{search_topic}" 引用',
            f'"{search_topic}" 原文',
            f'"{search_topic}" originally from',
            f'"{search_topic}" via',
        ]
        layer_results = []
        for tq in queries[:4]:
            try:
                br = await _beam_broad(tq, max_results=4)
                for r in br:
                    url = r.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        r["beam"] = "trace"
                        r["trace_layer"] = layer
                        layer_results.append(r)
            except Exception:
                continue
        return layer_results

    # 第1层：直接溯源
    layer1 = await _trace_one_layer(query, 1)
    results.extend(layer1)

    # 第2层：从第1层结果中找引用线索继续追
    if max_depth >= 2:
        for r in layer1[:3]:
            snippet = r.get("snippet", "") + " " + r.get("content", "")
            # 找引用的来源和被引用内容
            refs = re.findall(
                r"(?:via|引自|来源|source|cited from|reference)[:\s]*((?:https?://)?[^\s,;。]+)", snippet, re.IGNORECASE
            )
            refs += re.findall(r'https?://[^\s,;。"\')]+', snippet)

            for ref in refs[:2]:
                ref_url = ref if ref.startswith("http") else f"https://{ref}"
                if ref_url not in seen_urls:
                    seen_urls.add(ref_url)
                    ref_result = await _beam_crawl(ref_url, max_chars=3000)
                    for rr in ref_result:
                        rr["beam"] = "trace"
                        rr["trace_layer"] = 2
                        rr["trace_source"] = query
                        results.append(rr)

    # 去重
    seen_final = set()
    deduped = []
    for r in results:
        url = r.get("url", "")
        if url and url not in seen_final:
            seen_final.add(url)
            deduped.append(r)
    return deduped


async def _beam_cua(query: str) -> list[dict]:
    """
    CUA 波束 — 浏览器可视化操作。
    需要 YF-cua-tools 服务在线。
    """
    logger.info("🧪 CUA: %s", query)
    results = []

    # 调 CUA 服务
    cua_port = os.environ.get("CUA_PORT", "8888")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"http://127.0.0.1:{cua_port}/navigate",
                json={"url": f"https://www.google.com/search?q={quote_plus(query)}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                results.append(
                    {
                        "title": f"CUA 搜索结果: {query}",
                        "url": f"https://www.google.com/search?q={quote_plus(query)}",
                        "snippet": data.get("text", data.get("result", "CUA 执行完成"))[:500],
                        "source": "cua",
                        "beam": "cua",
                    }
                )
    except Exception as e:
        logger.warning("CUA 波束不可用: %s", e)

    return results


# 波束路由
BEAM_ROUTER = {
    "broad": _beam_broad,
    "sharp": _beam_sharp,
    "crawl": _beam_crawl,
    "deep": _beam_deep,
    "social": _beam_social,
    "trace": _beam_trace,
    "cua": _beam_cua,
}

BEAM_EMOJI = {
    "broad": "📡",
    "sharp": "🎯",
    "crawl": "🕷️",
    "deep": "🔬",
    "social": "💬",
    "trace": "🔗",
    "cua": "🧪",
}


# ════════════════════════════════════════════════════
# 结果融合
# ════════════════════════════════════════════════════


def _fuse_round_results(rounds: list[SearchRound], mission_keywords: set | None = None) -> dict:
    """融合所有轮次结果，去重归类+信源质量加权"""
    if mission_keywords is not None:
        return _fuse_with_quality(rounds, mission_keywords)
    # 老路径（无关键词时保持兼容）
    seen_urls = set()
    by_source = {}
    by_beam = {}
    for rd in rounds:
        for r in rd.results:
            url = r.get("url", "").split("?")[0].split("#")[0]
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            beam = r.get("beam", rd.beam_type)
            source = r.get("source", "unknown")
            if beam not in by_beam:
                by_beam[beam] = []
            if source not in by_source:
                by_source[source] = 0
            by_source[source] += 1
            by_beam[beam].append(r)
    return {
        "total_unique": len(seen_urls),
        "by_source": by_source,
        "by_beam": {b: len(items) for b, items in by_beam.items()},
        "results_by_beam": {
            b: [
                {"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("snippet", "")[:150]}
                for item in items[:8]
            ]
            for b, items in by_beam.items()
        },
    }


# ════════════════════════════════════════════════════
# 报告生成
# ════════════════════════════════════════════════════


def _generate_report(mission: str, rounds: list[SearchRound], coverage: CoverageAnalysis, fused: dict) -> dict:
    """生成结构化报告"""
    # 按轮次摘要
    round_summaries = []
    for rd in rounds:
        emoji = BEAM_EMOJI.get(rd.beam_type, "🔍")
        n = len(rd.results)
        elapsed = f"{rd.elapsed_ms / 1000:.1f}s" if rd.elapsed_ms else "-"
        round_summaries.append(
            {
                "round": rd.round_num,
                "beam": f"{emoji} {rd.beam_type}",
                "results": n,
                "elapsed": elapsed,
                "error": rd.error or None,
            }
        )

    confidence = round(coverage.coverage_rate / 100 * 0.7 + min(fused["total_unique"] / 20, 1) * 0.3, 2)

    return {
        "mission": mission,
        "status": "complete",
        "coverage_rate": coverage.coverage_rate,
        "confidence": min(confidence, 0.95),
        "rounds_executed": len(rounds),
        "total_unique_sources": fused["total_unique"],
        "rounds": round_summaries,
        "beam_summary": fused["by_beam"],
        "source_summary": fused["by_source"],
        "covered_topics": coverage.covered_topics,
        "gaps": coverage.uncovered_topics,
        "is_saturated": coverage.is_saturated,
        "_llm_enhanced": coverage.coverage_rate > 0 and coverage.coverage_rate < 100,
    }


# ════════════════════════════════════════════════════
# 🏛️ 主入口 — hive_mind
# ════════════════════════════════════════════════════


@tool()
async def hive_mind(mission: str, depth: str = "normal", timeout: int = 180) -> dict:
    """
    🐝 Hive Mind — 全能搜索子智能体。

    自动拆解搜索目标 → 选择合适波束 → 多轮扫描 → 缺口分析 → 交叉验证 → 出结构化报告。
    相当于一个完整的侦查小队，会自己规划、执行、拼图、汇报。

    参数:
        mission: 搜索目标/任务描述（越详细越好，包括要查什么维度）
        depth: 搜索深度 — "quick"（快扫2轮） | "normal"（标准3轮） | "full"（全量4轮）
        timeout: 最大执行秒数（默认 180s）

    返回:
        {
            "mission": 原始任务,
            "status": "complete",
            "coverage_rate": 覆盖度百分比,
            "confidence": 结果置信度 (0-1),
            "rounds_executed": 执行轮数,
            "total_unique_sources": 去重后的独立信源数,
            "rounds": [每轮执行摘要],
            "beam_summary": {波束: 结果数},
            "source_summary": {来源: 结果数},
            "covered_topics": 已覆盖的信息维度,
            "gaps": 未覆盖的信息缺口,
        }
    """
    start_time = time.time()
    logger.info("🐝 Hive Mind 启动: mission=%s | depth=%s | timeout=%d", mission, depth, timeout)

    # ── 提取任务关键词用于相关性评分（jieba 分词 + 中英混合） ──
    import re

    mission_clean = re.sub(r'[.,!?，。！？、：；（）()\[\]「」"\'\'\u00a0]', " ", mission)
    raw_tokens = mission_clean.split()
    mission_keywords = set()
    try:
        import jieba

        for token in raw_tokens:
            # 纯英文/数字token直接加入
            if re.match(r"^[a-zA-Z0-9._-]+$", token) and len(token) > 0:
                mission_keywords.add(token.lower())
            else:
                # 中文或中英混 — jieba 分词
                for w in jieba.cut(token, cut_all=False):
                    w = w.strip().lower()
                    if len(w) > 1 and w not in (
                        "的",
                        "了",
                        "是",
                        "在",
                        "和",
                        "也",
                        "就",
                        "都",
                        "而",
                        "且",
                        "有",
                        "与",
                        "或",
                        "对",
                        "被",
                        "中",
                    ):
                        mission_keywords.add(w)
    except ImportError:
        # 无 jieba 时回退: 中文按字符 bigram
        for token in raw_tokens:
            token = token.lower()
            if len(token) > 1:
                mission_keywords.add(token)
                if len(token) >= 4:
                    # 中文2字片段
                    for i in range(len(token) - 1):
                        pair = token[i : i + 2]
                        if any("a" <= c <= "z" for c in pair):  # 有英文不做
                            continue
                        if len(pair) == 2 and pair not in ("的", "了"):
                            mission_keywords.add(pair)
    # ── 第1步：分析任务，生成初始计划 → 策略自进化（Phase 5） ──
    base_plan = _plan_mission(mission, depth)
    plan = _self_evolve_plan(mission, depth, base_plan)
    if plan != base_plan:
        logger.info("  策略自进化已调整波束: %s", [p["beam"] for p in plan])
    logger.info("  初始计划: %d 轮 → %s", len(plan), [p["beam"] for p in plan])

    # ── 第2+N步：执行搜索轮次（LLM 驱动自适应计划） ──
    rounds: list[SearchRound] = []
    all_results_text = mission  # 累积文本
    max_rounds = len(plan) + 2  # 允许额外2轮自适应
    consecutive_saturated = 0

    for round_num in range(1, max_rounds + 1):
        # ── 超时检查 ──
        elapsed = time.time() - start_time
        if elapsed > timeout * 0.85:
            logger.warning("  超时临近 (%ds)，停止搜索", int(elapsed))
            break

        # ── LLM 缺口分析（每轮后都做，指导下一轮方向） ──
        last_analysis = await _analyze_coverage(mission, all_results_text)

        if last_analysis.is_saturated:
            consecutive_saturated += 1
            if consecutive_saturated >= 2:
                logger.info("  信息已饱和（连续2轮），提前停止")
                break
        else:
            consecutive_saturated = 0

        # ── 决定本轮波束和查询 ──
        if round_num <= len(plan):
            # 前N轮按初始计划走，但查询词根据缺口动态调整
            step = plan[round_num - 1]
            beam_type = step["beam"]
            query = step["query"]

            # 用缺口搜索词替换原查询（第2轮起）
            if round_num > 1 and last_analysis.expansions:
                query = last_analysis.expansions[0]
        else:
            # 超出初始计划时，LLM 驱动自适应
            if last_analysis.expansions:
                # 缺口最大 → 选缺失波束
                unused_beams = [b for b in BEAM_ROUTER if not any(b in r.beam_type for r in rounds)]
                if unused_beams:
                    beam_type = unused_beams[0]
                else:
                    beam_type = "deep"  # 全部用过就深度补查
                query = last_analysis.expansions[0]
                logger.info("  自适应轮次%d: 波束=%s 查询=%s", round_num, beam_type, query[:60])
            else:
                logger.info("  无缺口需补充，停止")
                break

        # ── 执行波束 ──
        handler = BEAM_ROUTER.get(beam_type)
        if not handler:
            continue

        rd = SearchRound(round_num=round_num, beam_type=beam_type, query=query)
        round_start = time.time()

        try:
            beam_results = await asyncio.wait_for(handler(query, 10), timeout=25)
            rd.results = beam_results
        except TimeoutError:
            rd.error = "超时 (25s)"
            logger.warning("  轮次%d %s 超时", round_num, beam_type)
        except Exception as e:
            rd.error = str(e)[:100]
            logger.warning("  轮次%d %s 失败: %s", round_num, beam_type, str(e)[:60])

        rd.elapsed_ms = int((time.time() - round_start) * 1000)
        rounds.append(rd)

        # 更新累积文本
        for r in rd.results:
            all_results_text += " " + r.get("title", "")
            all_results_text += " " + r.get("snippet", "")
            all_results_text += " " + r.get("content", "")[:300]

        emoji = BEAM_EMOJI.get(beam_type, "🔍")
        logger.info(
            "  轮次%d %s %s: %d 条 (%dms) | 覆盖=%s%% | 饱和=%s",
            round_num,
            emoji,
            beam_type,
            len(rd.results),
            rd.elapsed_ms,
            last_analysis.coverage_rate,
            last_analysis.is_saturated,
        )

    # ── 最终分析 + 融合（含信源质量 Phase 5） ──
    coverage = await _analyze_coverage(mission, all_results_text)
    fused = _fuse_round_results(rounds, mission_keywords)
    report = _generate_report(mission, rounds, coverage, fused)

    elapsed_total = time.time() - start_time
    # ── 相关性评分统计 ──
    all_results_for_score = []
    for rd in rounds:
        for r in rd.results:
            all_results_for_score.append(r)
    if all_results_for_score:
        relevances = [_score_result_relevance(r, mission_keywords) for r in all_results_for_score]
        avg_relevance = sum(relevances) / len(relevances) if relevances else 0.0
        high_relevance = sum(1 for s in relevances if s >= 0.6)
        low_relevance = sum(1 for s in relevances if s < 0.3)
        report["relevance_score"] = {
            "avg": round(avg_relevance, 2),
            "high": high_relevance,
            "low": low_relevance,
            "total_scored": len(relevances),
            "high_pct": round(high_relevance / len(relevances) * 100, 1) if relevances else 0,
        }
        logger.info(
            "  📈 相关性评分: avg=%.2f, high=%d/%d (%.1f%%)",
            avg_relevance,
            high_relevance,
            len(relevances),
            high_relevance / len(relevances) * 100 if relevances else 0,
        )

    report["elapsed_s"] = round(elapsed_total, 1)
    report["rounds_executed"] = len(rounds)

    logger.info(
        "🐝 Hive Mind 完成: 覆盖=%s%% | 信源=%d | 轮次=%d | %ds",
        coverage.coverage_rate,
        fused["total_unique"],
        len(rounds),
        int(elapsed_total),
    )

    # ── RSI Phase 1：搜索质量自评估 ──
    quality = _evaluate_search_quality(report)
    diagnoses = _diagnose_failure(quality)
    report["search_quality"] = quality
    if diagnoses:
        report["diagnoses"] = diagnoses
        logger.info("  📊 搜索质量评分: %.1f | 诊断: %d 项", quality.get("overall_score", 0), len(diagnoses))

    # ── RSI Phase 5：信源质量统计 ──
    fq = fused.get("quality", {})
    if fq:
        report["source_quality"] = {
            "avg_grade": fq.get("avg_grade", 0.5),
            "avg_combined": fq.get("avg_combined", 0.5),
        }
        logger.info(
            "  📊 信源质量: avg_grade=%.2f, avg_combined=%.2f", fq.get("avg_grade", 0.5), fq.get("avg_combined", 0.5)
        )

    # ── 方向3：自动 note 归档（含质量评分） ──
    _ = await _archive_to_notes(mission, report)

    # ── RSI Phase 3：搜索经验 → Knowledge 闭环 ──
    _ = await _archive_search_knowledge(mission, report, quality)

    # ── 方向4：多频时序扫描 ──
    timing = await _check_timing(mission, report)
    if timing:
        report["timing"] = timing

    return report


# ════════════════════════════════════════════════════
# RSI Phase 1：搜索质量自评估 & 失败模式诊断
# ════════════════════════════════════════════════════


def _evaluate_search_quality(report: dict) -> dict:
    """对一次 hive_mind 搜索做质量评分"""
    sr = report.get("source_summary", {})
    br = report.get("beam_summary", {})

    metrics = {
        "source_diversity": len(sr),
        "beam_diversity": len(br),
        "coverage_rate": report.get("coverage_rate", 0),
        "result_count": report.get("total_unique_sources", 0),
        "rounds_executed": report.get("rounds_executed", 0),
    }

    # 综合分
    score = (
        min(metrics["source_diversity"] / 10, 1) * 0.15
        + min(metrics["beam_diversity"] / 5, 1) * 0.10
        + metrics["coverage_rate"] / 100 * 0.40
        + min(metrics["result_count"] / 20, 1) * 0.20
        + min(metrics["rounds_executed"] / 4, 1) * 0.15
    ) * 100

    metrics["overall_score"] = round(score, 1)
    return metrics


def _diagnose_failure(metrics: dict) -> list[dict]:
    """低分时诊断失败模式"""
    if metrics.get("overall_score", 100) >= 60:
        return []

    diagnoses = []
    if metrics.get("beam_diversity", 0) <= 2 and metrics.get("result_count", 0) > 20:
        diagnoses.append({"pattern": "too_general", "fix": "任务太宽泛，建议拆成子任务分别搜索"})
    if metrics.get("result_count", 0) < 3:
        diagnoses.append({"pattern": "too_specific", "fix": "搜索结果太少，扩大搜索词范围"})
    if metrics.get("source_diversity", 0) <= 2 and metrics.get("result_count", 0) < 10:
        diagnoses.append({"pattern": "single_source", "fix": "结果集中在单一信源，需扩展引擎"})
    if metrics.get("coverage_rate", 100) < 30:
        diagnoses.append({"pattern": "low_coverage", "fix": "覆盖度过低，尝试调整波束组合"})
    return diagnoses


# ════════════════════════════════════════════════════
# 方向3：自动 note 归档
# ════════════════════════════════════════════════════


async def _archive_to_notes(mission: str, report: dict) -> bool:
    """hive_mind 搜索结果自动写入 L4 笔记系统"""
    try:
        from tools.note_tool import note_write

        gaps = report.get("gaps", []) or report.get("uncovered_topics", [])
        covered = report.get("covered_topics", []) or []

        content_parts = [
            "## 搜索结果摘要",
            f"- 覆盖度: {report.get('coverage_rate', 'N/A')}%- 置信度: {report.get('confidence', 'N/A')}",
            f"- 独立信源: {report.get('total_unique_sources', 'N/A')}",
            f"- 执行轮次: {report.get('rounds_executed', 'N/A')}",
            f"- 耗时: {report.get('elapsed_s', 'N/A')}s",
        ]
        if covered:
            content_parts.append("\n### 已覆盖\n" + "\n".join(f"- {t}" for t in covered[:10]))
        if gaps:
            content_parts.append("\n### 信息缺口\n" + "\n".join(f"- {t}" for t in gaps[:10]))

        content = "\n".join(content_parts)

        beam_summary = report.get("beam_summary", {})
        tags = f"hive_mind,{'full' if beam_summary else 'normal'}," + ",".join(list(beam_summary.keys())[:5])

        await note_write(
            title=f"搜索: {mission[:80]}",
            content=content,
            tags=tags,
            source="hive_mind",
        )
        logger.info("  ✅ 笔记已归档")
        return True
    except ImportError:
        logger.debug("  note_tool 不可用，跳过笔记归档")
    except Exception as e:
        logger.warning("  笔记归档失败: %s", str(e)[:80])
    return False


# ════════════════════════════════════════════════════
# RSI Phase 3：搜索经验 → Knowledge 闭环
# ════════════════════════════════════════════════════


async def _archive_search_knowledge(mission: str, report: dict, quality: dict) -> bool:
    """
    Phase 3：搜索经验自动写入 Knowledge 记忆系统。
    直接用 SQLite 写（绕过 Storage 可能存在的锁竞争），
    下次 kernel L2 自动注入到 system_prompt 中。
    """
    try:
        import json
        import sqlite3

        score = quality.get("overall_score", 0)
        diagnoses = report.get("diagnoses", [])
        beam_summary = report.get("beam_summary", {})
        coverage = report.get("coverage_rate", 0)
        sources = report.get("total_unique_sources", 0)

        # 只写有效经验：覆盖率>=50% 或 有诊断价值
        if score < 20 and coverage < 30:
            logger.info("  ⏭️ Phase 3 跳过：质量分 %d / 覆盖度 %d，无有效经验可沉淀", score, coverage)
            return False

        data_dir = Path(__file__).resolve().parent.parent / "data"
        db_path = data_dir / "entries.db"
        now = time.time()

        conn = sqlite3.connect(str(db_path))
        count = 0

        # 1. 有效搜索策略（高覆盖率时）
        if score >= 60 or coverage >= 60:
            # beam_summary 格式: {波束名: 结果数(int)} 或 {波束名: {有效信息}}
            effective_beams = list(beam_summary.keys())
            if effective_beams:
                entry = {
                    "mission": mission[:80],
                    "strategy": f"波束序列: {','.join(effective_beams[:6])}",
                    "coverage": coverage,
                    "sources": sources,
                    "quality_score": score,
                }
                summary = f"搜索策略: [{mission[:50]}] 有效波束: {','.join(effective_beams[:4])} | 覆盖度 {coverage}% | 信源 {sources}"
                conn.execute(
                    "INSERT INTO entries (type, content, summary, created_at, confidence) VALUES (?, ?, ?, ?, ?)",
                    (
                        "knowledge",
                        json.dumps(entry, ensure_ascii=False),
                        summary,
                        now,
                        "high" if score >= 70 else "medium",
                    ),
                )
                count += 1
                logger.info("  🧠 Phase 3: 有效策略写入 knowledge (score=%.0f)", score)

        # 2. 失败模式（低质量但有诊断价值）
        if diagnoses:
            for d in diagnoses[:3]:
                pattern = d.get("pattern", "")
                fix = d.get("fix", "")
                if pattern:
                    entry = {
                        "mission": mission[:60],
                        "pattern": pattern,
                        "fix": fix,
                        "quality": quality,
                    }
                    summary = f"搜索教训: [{mission[:40]}] {pattern} → {fix[:60]}"
                    conn.execute(
                        "INSERT INTO entries (type, content, summary, created_at, confidence) VALUES (?, ?, ?, ?, ?)",
                        ("knowledge", json.dumps(entry, ensure_ascii=False), summary, now, "medium"),
                    )
                    count += 1
            logger.info("  🧠 Phase 3: %d 条失败模式写入 knowledge", len(diagnoses))

        conn.commit()
        conn.close()

        if count > 0:
            # 也写 JSONL 镜像
            mirror_dir = data_dir / "knowledge"
            mirror_dir.mkdir(parents=True, exist_ok=True)
            mirror_path = mirror_dir / "opprime.jsonl"
            with open(mirror_path, "a", encoding="utf-8"):
                pass  # 保持文件存在即可

        logger.info("  🧠 Phase 3: 共写入 %d 条 knowledge", count)
        return count > 0

    except Exception as e:
        import traceback

        logger.warning("  Phase 3 知识归档失败: %s\n%s", str(e), traceback.format_exc()[:500])
        return False


# ════════════════════════════════════════════════════
# RSI Phase 5：信源质量分级 & 搜索策略自进化
# ════════════════════════════════════════════════════

# ——— 信源质量分级 ———
SOURCE_QUALITY_DB = {}  # domain -> {hits, good, bad, score}


def _grade_source(url: str) -> float:
    """对单个信源打分 0.0~1.0"""
    if not url:
        return 0.5
    from urllib.parse import urlparse

    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        return 0.5
    if not domain:
        return 0.5
    # 手动黑名单（SEO 垃圾站/聚合站）
    blacklist = {
        "zhuanlan.zhihu.com": 0.7,  # 知乎专栏还不错
        "baijiahao.baidu.com": 0.4,
        "sohu.com": 0.5,
        "it.sohu.com": 0.5,
        "163.com": 0.5,
        "dy.163.com": 0.4,
        "toutiao.com": 0.3,
        "36kr.com": 0.6,  # 还行
        "csdn.net": 0.5,
        "blog.csdn.net": 0.5,
        "cnblogs.com": 0.7,  # 博客园不错
        "jianshu.com": 0.5,
        "segmentfault.com": 0.8,
        "v2ex.com": 0.8,
        "oschina.net": 0.7,
        "infoq.cn": 0.8,
    }
    # 白名单
    whitelist = {
        "github.com": 0.95,
        "arxiv.org": 0.95,
        "paperswithcode.com": 0.95,
        "huggingface.co": 0.9,
        "docs.python.org": 0.95,
        "developer.mozilla.org": 0.95,
        "react.dev": 0.9,
        "nodejs.org": 0.9,
        "kaggle.com": 0.9,
        "stackoverflow.com": 0.85,
        "stackoverflow.blog": 0.85,
        "medium.com": 0.7,
        "dev.to": 0.8,
        "reddit.com": 0.7,
        "news.ycombinator.com": 0.85,
        "wikipedia.org": 0.85,
        "en.wikipedia.org": 0.85,
        "zh.wikipedia.org": 0.8,
    }
    # 检查子域名匹配
    for d, score in whitelist.items():
        if domain == d or domain.endswith("." + d):
            return score
    for d, score in blacklist.items():
        if domain == d or domain.endswith("." + d):
            return score
    # 未知站点：用 TLD 做粗略判断
    if domain.endswith((".edu", ".edu.cn")):
        return 0.85
    if domain.endswith((".gov", ".gov.cn")):
        return 0.85
    if domain.endswith(".org"):
        return 0.65
    return 0.6  # 默认中立


def _fuse_with_quality(rounds: list[SearchRound], mission_keywords: set) -> dict:
    """融合+信源质量加权"""
    seen_urls = {}
    by_source = {}
    by_beam = {}
    quality_scores = []

    for rd in rounds:
        for r in rd.results:
            url = r.get("url", "").split("?")[0].split("#")[0]
            if not url or url in seen_urls:
                continue
            seen_urls[url] = True

            beam = r.get("beam", rd.beam_type)
            source = r.get("source", "unknown")

            q = _grade_source(url)
            rel = _score_result_relevance(r, mission_keywords)
            combined = q * 0.4 + rel * 0.6

            quality_scores.append(
                {"url": url, "grade": round(q, 2), "relevance": round(rel, 2), "combined": round(combined, 2)}
            )

            r["_quality"] = round(q, 2)
            r["_relevance"] = round(rel, 2)

            if beam not in by_beam:
                by_beam[beam] = []
            if source not in by_source:
                by_source[source] = {"count": 0, "quality_sum": 0.0}
            by_source[source]["count"] += 1
            by_source[source]["quality_sum"] += q
            by_beam[beam].append(r)

    source_details = {}
    for s, v in by_source.items():
        source_details[s] = {
            "count": v["count"],
            "avg_quality": round(v["quality_sum"] / v["count"], 2) if v["count"] else 0,
        }

    avg_quality = round(sum(q["grade"] for q in quality_scores) / len(quality_scores), 2) if quality_scores else 0.5
    avg_combined = round(sum(q["combined"] for q in quality_scores) / len(quality_scores), 2) if quality_scores else 0.5

    return {
        "total_unique": len(seen_urls),
        "by_source": source_details,
        "by_beam": {b: len(items) for b, items in by_beam.items()},
        "results_by_beam": {
            b: [
                {"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("snippet", "")[:150]}
                for item in items[:8]
            ]
            for b, items in by_beam.items()
        },
        "quality": {
            "avg_grade": avg_quality,
            "avg_combined": avg_combined,
            "scores": quality_scores[:50],
        },
    }


# ——— 搜索策略自进化 ———


def _load_strategy_history() -> list[dict]:
    """从 knowledge 表读取历史有效波束策略"""
    try:
        import json
        import sqlite3
        from pathlib import Path

        db_path = Path(__file__).resolve().parent.parent / "data" / "entries.db"
        if not db_path.exists():
            return []
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT content FROM entries WHERE type='knowledge' AND content LIKE '%strategy%' ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        conn.close()
        strategies = []
        for row in rows:
            try:
                d = json.loads(row[0])
                if d.get("strategy"):
                    strategies.append(d)
            except Exception:
                continue
        return strategies
    except Exception:
        return []


def _self_evolve_plan(mission: str, depth: str, base_plan: list) -> list:
    """基于历史策略优化波束序列"""
    strategies = _load_strategy_history()
    if not strategies:
        return base_plan

    # 找最相似的历史策略（关键词交叠）
    mission_lower = mission.lower()
    mission_words = set(mission_lower.split()[:6])
    if not mission_words:
        return base_plan

    best = None
    best_overlap = 0
    for s in strategies:
        hs = (s.get("mission", "") or "").lower()
        hw = set(hs.split()[:6])
        overlap = len(mission_words & hw)
        if overlap > best_overlap:
            best_overlap = overlap
            best = s

    if not best or best_overlap == 0:
        return base_plan

    strategy_str = best.get("strategy", "") or ""
    if "波束序列:" not in strategy_str:
        return base_plan

    beams = [b.strip() for b in strategy_str.replace("波束序列:", "").split(",") if b.strip() in BEAM_ROUTER]
    if not beams:
        return base_plan

    logger.info(
        "  🔄 策略自进化: 从历史匹配 %s 覆盖 %d 词 → 使用波束 %s", best.get("mission", "")[:30], best_overlap, beams
    )

    # 替换 base_plan 的波束序列
    evolved = []
    for i, step in enumerate(base_plan):
        evolved_step = dict(step)
        if i < len(beams):
            evolved_step["beam"] = beams[i]
        evolved.append(evolved_step)
    return evolved


# ════════════════════════════════════════════════════
# 方向4：多频时序扫描
# ════════════════════════════════════════════════════


async def _check_timing(mission: str, report: dict) -> dict | None:
    """检查同一任务是否有历史搜索记录，做时序对比"""
    try:
        from tools.note_tool import note_search

        # 用 mission 前30字搜历史笔记
        key = mission[:30]
        history = await note_search(query=key, max_results=5)
        notes = history.get("notes", [])

        relevant = []
        for note in notes:
            title = note.get("title", "")
            if "搜索:" in title:
                relevant.append(note)

        if not relevant:
            return None

        prev = relevant[0]
        timing_info = {
            "previous_search": prev.get("created_at", "未知"),
            "previous_file": prev.get("file", ""),
            "previous_observations": prev.get("observations", 0),
            "note": f"同任务已有 {len(relevant)} 次历史搜索记录，最晚为 {prev.get('created_at', '未知')}",
        }
        return timing_info
    except ImportError:
        return None
    except Exception as e:
        logger.debug("时序检查失败: %s", str(e)[:60])
    return None


# ════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="🐝 Hive Mind — 全能搜索子智能体")
    parser.add_argument("--mission", "-m", required=True, help="搜索任务描述")
    parser.add_argument("--depth", "-d", default="normal", choices=["quick", "normal", "full"])
    parser.add_argument("--timeout", "-t", type=int, default=180)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    result = asyncio.run(hive_mind(args.mission, args.depth, args.timeout))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print("🐝 Hive Mind 搜索结果")
        print(f"任务: {result['mission']}")
        print(f"状态: {result['status']}")
        print(f"覆盖度: {result['coverage_rate']}% | 置信度: {result['confidence']}")
        print(
            f"轮次: {result['rounds_executed']} | 信源: {result['total_unique_sources']} | 耗时: {result['elapsed_s']}s"
        )
        print(f"{'=' * 60}")

        print(f"\n📊 波束分布: {result['beam_summary']}")
        print(f"📊 来源分布: {result['source_summary']}")
        print(f"\n✅ 已覆盖: {result['covered_topics']}")
        print(f"❌ 缺口: {result['gaps']}")

        if not result.get("is_saturated"):
            print("\n⚠️ 信息尚未饱和，建议再指定搜索方向或换关键词")

        print(f"\n{'─' * 60}")
        for rs in result.get("rounds", []):
            print(f"  {rs['round']}. {rs['beam']}: {rs['results']}条 ({rs['elapsed']})")
            if rs.get("error"):
                print(f"     ❌ {rs['error']}")
