# SPDX-License-Identifier: MIT
"""
gbase/lib/auto_learn.py

Autonomous learning engine — enables GBase to proactively learn from the web and persist knowledge.

Design principles:
- Runs independently, delivers report via configured channel
- Supports both RSS and search learning modes
- Learning results auto-persist to knowledge layer
- Does not interfere with normal conversation processing
"""

import json
import logging
import os
import time
from pathlib import Path

from lib.rss_fetcher import fetch_topic, load_rss_topics

logger = logging.getLogger(__name__)

# Legacy search-mode learning topics (backward compatible)
DEFAULT_LEARN_TOPICS = [
    {
        "topic": "Latest AI Industry News",
        "search_queries": [
            "artificial intelligence AI May 2026 latest news",
            "AI industry news May 2026",
        ],
        "category": "ai_news",
        "description": "Track major AI industry news, funding, product launches",
    },
]


# Config file path
CONFIG_DIR = Path(__file__).parent.parent / "data"
TOPICS_PATH = CONFIG_DIR / "learn_topics.json"


def _ensure_config():
    """Ensure learning topic config file exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not TOPICS_PATH.exists():
        with open(TOPICS_PATH, "w", encoding="utf-8") as f:
            json.dump({"topics": DEFAULT_LEARN_TOPICS, "version": "1.0"}, f, ensure_ascii=False, indent=2)
        logger.info("Created default learning topic config: %s", TOPICS_PATH)


def load_topics() -> list[dict]:
    """Load search-mode learning topic config."""
    _ensure_config()
    with open(TOPICS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("topics", DEFAULT_LEARN_TOPICS)


def save_topics(topics: list[dict]):
    """Save search-mode learning topic config."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(TOPICS_PATH, "w", encoding="utf-8") as f:
        json.dump({"topics": topics, "version": "1.0"}, f, ensure_ascii=False, indent=2)
    logger.info("Search learning topic config updated: %d topics", len(topics))


# ── Learning result formatting ──────────────────────────────────────────


def format_learn_result(
    topic_name: str,
    total: int,
    saved: int,
    errors: int,
    elapsed: float,
    highlights: list[str],
    analysis_comment: str = "",
) -> str:
    """Generate learning task report text for delivery."""
    lines = [
        f"📚 Auto-learning complete: {topic_name}",
        f"⏱️ Time: {elapsed:.0f}s",
    ]
    if total > 0:
        lines.append(f"📊 Collected {total} items")
    if saved > 0:
        lines.append(f"✅ Persisted {saved} items to memory")
    if errors > 0:
        lines.append(f"⚠️ {errors} errors")
    if highlights:
        lines.append("")
        lines.append("📌 Highlights:")
        for h in highlights[:3]:
            lines.append(f"- {h}")
    if analysis_comment:
        lines.append("")
        lines.append("🔍 Analyst comment:")
        comment = analysis_comment[:600]
        for line in comment.split("\n"):
            lines.append(f"  {line}")
    return "\n".join(lines)


# ── Learning task executor ───────────────────────────────────────


class AutoLearner:
    """Autonomous learning executor.

    Triggered by the scheduler, processes configured learning topics:
    RSS => LLM understanding => analysis (cross-source) => self-reflection => report
    Learning is not the destination; self-evolution is. Each learning cycle must produce an evolution plan.
    Falls back to search mode if no RSS sources configured.
    """

    def __init__(self, sender_func=None, kernel_run_func=None):
        """Initialize.

        Args:
            sender_func: delivery function, async (open_id, text) -> None
            kernel_run_func: kernel execution function, async (message, platform, session) -> str
                            session=None creates a one-shot session
        """
        self._sender = sender_func
        self._kernel_run = kernel_run_func
        self._owner_id = os.environ.get("OPPRIME_LEARN_OWNER", "")

    def set_owner(self, open_id: str):
        """Set learning report delivery target."""
        self._owner_id = open_id

    # ── RSS mode ──

    async def _execute_rss_topic(self, topic: dict) -> dict:
        """Execute one learning topic via RSS mode.

        Full workflow (5-step closed loop):
        1. Fetch RSS feeds
        2. LLM reads articles, remember_fact stores facts (fact layer)
        3. Analyst mode: cross-source correlation => logic chain analysis => opinion output => remember_fact stores conclusions (cognition layer)
        4. Self-evolution reflection: how can this knowledge drive self-evolution? Write to evolution-log.md
        5. Report (with analyst comments)
        """
        start = time.time()

        # Fetch RSS first
        rss_result = await fetch_topic(topic)
        content = rss_result.get("content", "")
        article_count = rss_result.get("article_count", 0)
        error_count = rss_result.get("error_count", 0)
        errors = rss_result.get("errors", [])

        topic_name = topic.get("topic", "Unknown topic")

        if article_count == 0:
            # No articles fetched, still report
            elapsed = time.time() - start
            result = {
                "topic": topic_name,
                "total": 0,
                "saved": 0,
                "errors": error_count,
                "elapsed": elapsed,
                "highlights": [f"No new articles ({', '.join(errors[:2])})"],
                "analysis_comment": "",
            }
            if self._owner_id and self._sender:
                report = format_learn_result(
                    topic_name,
                    0,
                    0,
                    error_count,
                    elapsed,
                    [f"No new articles ({', '.join(errors[:2])} etc.)"],
                )
                try:
                    await self._sender(self._owner_id, report)
                except Exception as e:
                    logger.error("Learning report delivery failed: %s", e)
            return result

        # Build learning instruction: feed RSS results to LLM (with analyst mode)
        learn_msg = (
            f"📡 Auto-learning task: Read the following RSS articles and complete the full learning loop.\n\n"
            f"## Topic\n{topic_name}\n\n"
            f"## RSS Source Summary\n{content}\n\n"
            f"## Learning Steps (5 steps, execute in order)\n\n"
            f"### Step 1: Fact persistence\n"
            f"- Carefully read all RSS articles above\n"
            f"- For each article worth remembering long-term, use remember_fact to store in memory\n"
            f"  (only store long-term valuable facts, not one-off news. Use category='general')\n\n"
            f"### Step 2: Analyst mode — Cross-source correlation\n"
            f"- Review all articles fetched this time, find their **internal connections**\n"
            f"- Ask yourself: Which articles seem unrelated but are actually about the same thing?\n"
            f"- Ask yourself: Which articles have causal, progressive, or conflicting relationships?\n\n"
            f"### Step 3: Analyst mode — Logic chain analysis\n"
            f"- Organize the connections into **logic chains**: A => B => C => Conclusion\n"
            "- Example: Tech X emerges -> solves problem Y -> enables Z -> industry trend is...\n"
            f"- Logic chains must have causal progression, not just parallel enumeration\n\n"
            f"### Step 4: Analyst mode — Opinion output + persistence\n"
            f"- Based on the analysis, output an **opinionated analysis comment**\n"
            f"- Format:\n"
            f"  ```\n"
            f"  ✳️ I see a clear feedback loop forming:\n"
            f"  [Info point A] + [Info point B] => [logic chain] => [conclusion/insight]\n"
            f"  ```\n"
            f"- **Required**: use remember_fact to store analysis conclusion in memory\n"
            f"  - fact content: write the full analysis comment\n"
            f"  - category: 'analysis'\n"
            f"  - tags: 'analysis,insight,{topic_name}'\n\n"
            f"### Step 5: Self-evolution reflection\n"
            f"- Ask yourself Q1: How does this learning help my self-evolution?\n"
            f"- Ask yourself Q2: How can I use this learning for self-evolution?\n"
            f"- Based on this reflection, produce a concrete self-evolution plan and write to evolution-log.md\n\n"
            f"## Final response requirements\n"
            f"After completing all steps, reply with the following report format:\n"
            f"1. What I learned (2-3 sentence summary)\n"
            f"2. Self-evolution plan\n"
            f"3. Analyst comment (start with ✳️)\n\n"
            f"Note: This is an autonomous learning task. Execute automatically without asking for input.\n"
            f"Learning is not the destination; self-evolution is. Every learning cycle must produce an evolution plan.\n"
            f"The analyst comment is the core output — transform information into cognition, persist cognition as knowledge."
        )

        try:
            reply = await self._kernel_run(
                learn_msg,
                platform="auto_learn",
                session=None,
            )
            saved = 1 if reply and len(reply) > 20 else 0
            highlights = [reply[:120]] if saved else []
            # Extract analyst comment section (lines starting with ✳️)
            analysis_comment = ""
            if reply:
                for line in reply.split("\n"):
                    if line.strip().startswith("✳️"):
                        analysis_comment = line.strip()
                        break
        except Exception as e:
            logger.error("RSS learning %s LLM ingestion failed: %s", topic_name, e)
            reply = ""
            saved = 0
            highlights = []
            analysis_comment = ""
            error_count += 1

        elapsed = time.time() - start
        result = {
            "topic": topic_name,
            "total": article_count,
            "saved": saved,
            "errors": error_count,
            "elapsed": elapsed,
            "highlights": highlights,
            "analysis_comment": analysis_comment,
        }

        # Report
        if self._owner_id and self._sender:
            report = format_learn_result(
                topic_name,
                article_count,
                saved,
                error_count,
                elapsed,
                highlights,
                analysis_comment,
            )
            try:
                await self._sender(self._owner_id, report)
            except Exception as e:
                logger.error("Learning report delivery failed: %s", e)

        return result

    # ── Search mode (legacy) ──

    async def execute_learn_topic(self, topic: dict) -> dict:
        """Execute one learning topic (search mode).

        Returns:
            {
                "topic": topic name,
                "total": total results,
                "saved": persisted count,
                "errors": error count,
                "elapsed": time used (seconds),
                "highlights": highlighted info list,
                "analysis_comment": analyst comment,
            }
        """
        start = time.time()
        topic_name = topic.get("topic", "Unknown topic")
        queries = topic.get("search_queries", [])

        total = 0
        saved = 0
        errors = 0
        highlights = []
        analysis_comment = ""

        for query in queries:
            try:
                learn_msg = (
                    f"📡 Auto-learning task: Complete one learning cycle as follows.\n\n"
                    f"## Topic\n{topic_name}\n\n"
                    f"## Learning Steps (5 steps, execute in order)\n\n"
                    f"### Step 1: Search\n"
                    f"- Use search_web to search: {query}\n"
                    f"- Read the results, understand core information\n\n"
                    f"### Step 2: Fact persistence\n"
                    f"- If you find facts worth remembering long-term, use remember_fact to store in memory\n"
                    f"  (category='general')\n\n"
                    f"### Step 3: Analyst mode — Cross-source correlation + logic chain\n"
                    f"- Find internal connections among the information found this time\n"
                    f"- Organize into logic chains: A => B => C => Conclusion\n\n"
                    f"### Step 4: Analyst mode — Opinion output + persistence\n"
                    f"- Output an opinionated analysis comment (start with ✳️)\n"
                    f"- Use remember_fact to store the analysis conclusion (category='analysis')\n\n"
                    f"### Step 5: Self-evolution reflection\n"
                    f"- Ask yourself: How does this help my self-evolution?\n"
                    f"- Produce a concrete self-evolution plan, write to evolution-log.md\n\n"
                    f"## Final response requirements\n"
                    f"1. What I learned (2-3 sentence summary)\n"
                    f"2. Self-evolution plan\n"
                    f"3. Analyst comment (start with ✳️)\n\n"
                    f"Note: This is an autonomous learning task. Execute automatically."
                )

                reply = await self._kernel_run(
                    user_message=learn_msg,
                    platform="auto_learn",
                    session=None,
                )
                total += 1
                if reply and len(reply) > 20:
                    saved += 1
                    highlights.append(reply[:120])
                    # Extract analyst comment
                    for line in reply.split("\n"):
                        if line.strip().startswith("✳️"):
                            analysis_comment = line.strip()
                            break
            except Exception as e:
                logger.error("Search learning %s execution failed: %s", topic_name, e)
                errors += 1

        elapsed = time.time() - start
        result = {
            "topic": topic_name,
            "total": total,
            "saved": saved,
            "errors": errors,
            "elapsed": elapsed,
            "highlights": highlights,
            "analysis_comment": analysis_comment,
        }

        # Report
        if self._owner_id and self._sender:
            report = format_learn_result(
                topic_name,
                total,
                saved,
                errors,
                elapsed,
                highlights,
                analysis_comment,
            )
            try:
                await self._sender(self._owner_id, report)
            except Exception as e:
                logger.error("Learning report delivery failed: %s", e)

        return result

    # ── Full learning entry point ──

    async def learn_all_topics(self, topic_filter: list[str] | None = None) -> list[dict]:
        """Execute all learning topics.

        Args:
            topic_filter: if provided, only learn topics whose name contains filter keywords

        Returns:
            List of per-topic learning results
        """
        # Load RSS topics first
        rss_topics = load_rss_topics()
        # Then load search topics
        search_topics = load_topics()

        all_topics = []
        for t in rss_topics:
            all_topics.append({**t, "_mode": "rss"})
        for t in search_topics:
            all_topics.append({**t, "_mode": "search"})

        results = []

        for topic in all_topics:
            topic_name = topic.get("topic", "Unknown topic")

            # Filter
            if topic_filter:
                matched = False
                for keyword in topic_filter:
                    if keyword.lower() in topic_name.lower():
                        matched = True
                        break
                if not matched:
                    continue

            logger.info("Starting learning: %s (mode: %s)", topic_name, topic.get("_mode"))

            try:
                if topic.get("_mode") == "rss":
                    result = await self._execute_rss_topic(topic)
                else:
                    result = await self.execute_learn_topic(topic)
                results.append(result)
            except Exception as e:
                logger.error("Learning topic %s exception: %s", topic_name, e)
                results.append(
                    {
                        "topic": topic_name,
                        "total": 0,
                        "saved": 0,
                        "errors": 1,
                        "elapsed": 0,
                        "highlights": [f"Exception: {str(e)[:80]}"],
                        "analysis_comment": "",
                    }
                )

        return results
