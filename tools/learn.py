# SPDX-License-Identifier: MIT
"""
gbase/tools/learn.py

Self-learning tool — LLM configures learning direction.

add_learn_topic: Add a learning topic (RSS priority, search as fallback)
list_learn_topics: List configured learning topics
remove_learn_topic: Remove a learning topic
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
    """Add a self-learning topic.

    Call this when the user asks to set up a learning direction.
    Supports RSS feeds (priority) and search keywords (fallback) modes.

    For example, if the user says "learn AI industry news daily", create a
    topic named ai_news and pass in RSS sources.

    For search-only topics (e.g., "check weather trends in the south"),
    pass only search_queries, not rss_sources.

    Args:
        topic: Learning topic name, e.g. "AI industry latest news"
        rss_sources: RSS source list, as a JSON-format string. Format:
            [{"name":"source_name","url":"RSS_URL","lang":"en"}]
            Multiple sources as a JSON array. Optional; RSS mode activates
            when at least one is provided.
        search_queries: Search keywords, comma-separated. Optional, as a
            supplement to RSS mode or the primary source for search-only mode.
            e.g. "AI latest news, artificial intelligence news"
        category: Category. Options: ai_news / frontend / writing / engineering / general
        description: Topic description, briefly explain what this topic learns
    """
    rss = []
    srchs = []

    # Parse RSS sources (JSON format)
    if rss_sources.strip():
        try:
            rss = json.loads(rss_sources)
            if not isinstance(rss, list):
                return {"error": "rss_sources must be a JSON array"}
        except json.JSONDecodeError as e:
            return {"error": f"RSS source parsing failed: {e}, please provide a valid JSON-formatted RSS source list"}

    # Parse search keywords
    if search_queries.strip():
        srchs = [q.strip() for q in search_queries.split(",") if q.strip()]

    if not rss and not srchs:
        return {
            "error": "At least one RSS source or search keyword is required. If no known RSS sources, pass at least search_queries."
        }

    # Determine mode and write to the corresponding config
    if rss:
        # RSS mode
        rss_topics = load_rss_topics()
        for t in rss_topics:
            if t["topic"] == topic:
                return {"error": f"RSS learning topic '{topic}' already exists"}

        entry = {
            "topic": topic,
            "category": category,
            "description": description or topic,
            "rss_sources": rss,
        }
        rss_topics.append(entry)
        save_rss_topics(rss_topics)
        logger.info("RSS learning topic added: %s (%d sources)", topic, len(rss))
        source_names = ", ".join(s.get("name", "") for s in rss)
        return {"result": f"Added RSS learning topic '{topic}', {len(rss)} RSS source(s): {source_names}"}

    else:
        # Search mode
        topics = load_topics()
        for t in topics:
            if t["topic"] == topic:
                return {"error": f"Search learning topic '{topic}' already exists"}

        entry = {
            "topic": topic,
            "search_queries": srchs,
            "category": category,
            "description": description or topic,
        }
        topics.append(entry)
        save_topics(topics)
        logger.info("Search learning topic added: %s (%d keywords)", topic, len(srchs))
        return {"result": f"Added search learning topic '{topic}', {len(srchs)} search keyword(s)"}


@tool()
async def list_learn_topics() -> dict:
    """List all configured self-learning topics (RSS + search)."""
    rss_topics = load_rss_topics()
    search_topics = load_topics()

    lines = []

    # RSS mode
    if rss_topics:
        lines.append("📡 RSS learning topics (priority):")
        for t in rss_topics:
            sources = t.get("rss_sources", [])
            source_names = ", ".join(s.get("name", "") for s in sources)
            lines.append(f"  - {t['topic']}")
            lines.append(f"    RSS sources: {source_names}")
            if t.get("description"):
                lines.append(f"    {t['description']}")
        lines.append("")

    # Search mode
    if search_topics:
        lines.append("🔍 Search learning topics (fallback):")
        for t in search_topics:
            queries = ", ".join(t.get("search_queries", []))
            lines.append(f"  - {t['topic']}")
            lines.append(f"    Search: {queries}")
        lines.append("")

    if not rss_topics and not search_topics:
        return {"result": "No learning topics configured. Use add_learn_topic to add one."}

    total = len(rss_topics) + len(search_topics)
    lines.insert(0, f"Total {total} learning topic(s) (RSS {len(rss_topics)} + search {len(search_topics)}):")
    return {"result": "\n".join(lines), "total": total}


@tool()
async def remove_learn_topic(topic: str) -> dict:
    """Remove a learning topic.

    Checks both RSS and search configurations.

    Args:
        topic: Name of the learning topic to remove
    """
    # Try RSS
    rss_topics = load_rss_topics()
    before_rss = len(rss_topics)
    rss_topics = [t for t in rss_topics if t["topic"] != topic]
    removed_rss = before_rss - len(rss_topics)
    if removed_rss > 0:
        save_rss_topics(rss_topics)

    # Try search
    search_topics = load_topics()
    before_s = len(search_topics)
    search_topics = [t for t in search_topics if t["topic"] != topic]
    removed_s = before_s - len(search_topics)
    if removed_s > 0:
        save_topics(search_topics)

    removed = removed_rss + removed_s
    if removed == 0:
        return {"error": f"Learning topic '{topic}' not found"}

    logger.info("Learning topic removed: %s", topic)
    return {"result": f"Removed learning topic '{topic}'"}
