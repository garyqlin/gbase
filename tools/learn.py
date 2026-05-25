# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/learn.py

Self-learning tool — LLM configures learning direction.

add_learn_topic: 添加一个学习方向（RSS 优先，支持搜索备用）
list_learn_topics: 查看已配置的学习方向
remove_learn_topic: 删除一个学习方向
"""

import json
import logging

from lib.auto_learn import load_topics, save_topics
from lib.rss_fetcher import load_rss_topics, save_rss_topics
from lib.toolkit import tool

logger = logging.getLogger(__name__)


@tool()
async def add_learn_topic(
    topic: str, rss_sources: str = "", search_queries: str = "", category: str = "general", description: str = ""
) -> dict:
    """添加一个自主学习方向。

    用户让你设定学习方向时调用。
    支持 RSS 源（优先）和搜索关键词（备用）两种模式。

    例如用户说"每天学习一下AI行业新闻"，你就创建一个 ai_news 方向，
    把 RSS 源传进去。

    如果是传统搜索方向（如"帮我查一下南方天气变化趋势"），
    只传 search_queries，不传 rss_sources。

    Args:
        topic: 学习方向名称，如"人工智能行业最新动态"
        rss_sources: RSS 源列表，JSON 格式的字符串。格式：
            [{"name":"源名","url":"RSS地址","lang":"zh"}]
            多个源用 JSON 数组。可选，至少传一个时开启 RSS 模式。
        search_queries: 搜索关键词，逗号分隔。可选，作为 RSS 模式的补充
            或纯搜索模式的主要来源。如"AI 最新动态,人工智能 新闻"
        category: 分类。可选：ai_news / frontend / writing / engineering / general
        description: 方向描述，简要说明这个方向学什么
    """
    rss = []
    srchs = []

    # 解析 RSS 源（JSON 格式）
    if rss_sources.strip():
        try:
            rss = json.loads(rss_sources)
            if not isinstance(rss, list):
                return {"error": "rss_sources 必须是 JSON 数组格式"}
        except json.JSONDecodeError as e:
            return {"error": f"RSS 源解析失败: {e}，请提供正确 JSON 格式的 RSS 源列表"}

    # 解析搜索关键词
    if search_queries.strip():
        srchs = [q.strip() for q in search_queries.split(",") if q.strip()]

    if not rss and not srchs:
        return {"error": "至少需要一个 RSS 源或搜索关键词。如果没有已知的 RSS 源，至少传 search_queries。"}

    # 判断模式并写入对应配置
    if rss:
        # RSS 模式
        rss_topics = load_rss_topics()
        for t in rss_topics:
            if t["topic"] == topic:
                return {"error": f"RSS 学习方向「{topic}」已存在"}

        entry = {
            "topic": topic,
            "category": category,
            "description": description or topic,
            "rss_sources": rss,
        }
        rss_topics.append(entry)
        save_rss_topics(rss_topics)
        logger.info("RSS 学习方向已添加: %s (%d 个源)", topic, len(rss))
        source_names = ", ".join(s.get("name", "") for s in rss)
        return {"result": f"已添加 RSS 学习方向「{topic}」，{len(rss)} 个 RSS 源：{source_names}"}

    else:
        # 搜索模式
        topics = load_topics()
        for t in topics:
            if t["topic"] == topic:
                return {"error": f"搜索学习方向「{topic}」已存在"}

        entry = {
            "topic": topic,
            "search_queries": srchs,
            "category": category,
            "description": description or topic,
        }
        topics.append(entry)
        save_topics(topics)
        logger.info("搜索学习方向已添加: %s (%d 个关键词)", topic, len(srchs))
        return {"result": f"已添加搜索学习方向「{topic}」，{len(srchs)} 个搜索关键词"}


@tool()
async def list_learn_topics() -> dict:
    """列出已配置的所有自主学习方向（RSS + 搜索）。"""
    rss_topics = load_rss_topics()
    search_topics = load_topics()

    lines = []

    # RSS 模式
    if rss_topics:
        lines.append("📡 RSS 学习方向（优先）：")
        for t in rss_topics:
            sources = t.get("rss_sources", [])
            source_names = ", ".join(s.get("name", "") for s in sources)
            lines.append(f"  - {t['topic']}")
            lines.append(f"    RSS 源: {source_names}")
            if t.get("description"):
                lines.append(f"    {t['description']}")
        lines.append("")

    # 搜索模式
    if search_topics:
        lines.append("🔍 搜索学习方向（备用）：")
        for t in search_topics:
            queries = ", ".join(t.get("search_queries", []))
            lines.append(f"  - {t['topic']}")
            lines.append(f"    搜索: {queries}")
        lines.append("")

    if not rss_topics and not search_topics:
        return {"result": "没有配置任何学习方向。使用 add_learn_topic 添加。"}

    total = len(rss_topics) + len(search_topics)
    lines.insert(0, f"共 {total} 个学习方向（RSS {len(rss_topics)} + 搜索 {len(search_topics)}）：")
    return {"result": "\n".join(lines), "total": total}


@tool()
async def remove_learn_topic(topic: str) -> dict:
    """删除一个学习方向。

    会同时检查 RSS 和搜索配置。

    Args:
        topic: 要删除的学习方向名称
    """
    # 尝试 RSS
    rss_topics = load_rss_topics()
    before_rss = len(rss_topics)
    rss_topics = [t for t in rss_topics if t["topic"] != topic]
    removed_rss = before_rss - len(rss_topics)
    if removed_rss > 0:
        save_rss_topics(rss_topics)

    # 尝试搜索
    search_topics = load_topics()
    before_s = len(search_topics)
    search_topics = [t for t in search_topics if t["topic"] != topic]
    removed_s = before_s - len(search_topics)
    if removed_s > 0:
        save_topics(search_topics)

    removed = removed_rss + removed_s
    if removed == 0:
        return {"error": f"未找到学习方向「{topic}」"}

    logger.info("学习方向已删除: %s", topic)
    return {"result": f"已删除学习方向「{topic}」"}
