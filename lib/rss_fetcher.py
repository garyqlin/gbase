# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/rss_fetcher.py

RSS fetcher — fetch and parse RSS feeds into structured data.

Pure stdlib implementation (urllib + xml.etree.ElementTree), no external RSS libraries.
"""

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════


@dataclass
class RssItem:
    """A single RSS article."""

    title: str
    link: str
    description: str = ""
    pub_date: str = ""
    source_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RssFeed:
    """Fetch result for a single RSS source."""

    source_name: str
    rss_url: str
    items: list = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "rss_url": self.rss_url,
            "items": [i.to_dict() for i in self.items],
            "fetched_at": self.fetched_at,
            "error": self.error,
        }


# ═══════════════════════════════════════════════════
# RSS Source Configuration for Learning Topics
# ═══════════════════════════════════════════════════

DEFAULT_RSS_TOPICS = [
    {
        "topic": "AI Industry Latest News",
        "category": "ai_news",
        "description": "Track major AI news, product releases, open-source projects",
        "rss_sources": [
            {"name": "Hacker News", "url": "https://hnrss.org/frontpage", "lang": "en"},
            {"name": "ArXiv AI", "url": "http://export.arxiv.org/rss/cs.AI", "lang": "en"},
            {"name": "ArXiv ML", "url": "http://export.arxiv.org/rss/cs.LG", "lang": "en"},
            {"name": "Synced", "url": "https://jiqizhixin.com/feed", "lang": "zh"},
            {"name": "QbitAI", "url": "https://www.qbitai.com/feed", "lang": "zh"},
            {"name": "36Kr AI", "url": "https://rsshub.app/36kr/motif/ai", "lang": "zh"},
            {"name": "Zhihu AI Daily", "url": "https://rsshub.app/zhihu/hotlist", "lang": "zh"},
            {"name": "PaperWeekly", "url": "https://rsshub.app/paperweekly/zhuanlan", "lang": "zh"},
            {"name": "GitHub Trending", "url": "https://rsshub.app/github/trending/daily", "lang": "en"},
            {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/feed/", "lang": "en"},
            {"name": "The Verge AI", "url": "https://www.theverge.com/rss/index.xml", "lang": "en"},
        ],
    },
    {
        "topic": "Frontend Tech & Web Development",
        "category": "frontend",
        "description": "Track frontend tech trends, CSS/JS features, design trends",
        "rss_sources": [
            {"name": "CSS-Tricks", "url": "https://css-tricks.com/feed/", "lang": "en"},
            {"name": "Smashing Magazine", "url": "https://www.smashingmagazine.com/feed/", "lang": "en"},
            {"name": "A List Apart", "url": "https://alistapart.com/main/feed/", "lang": "en"},
            {"name": "WebKit Blog", "url": "https://webkit.org/blog/feed/", "lang": "en"},
            {"name": "The New Stack", "url": "https://thenewstack.io/feed/", "lang": "en"},
            {"name": "InfoQ", "url": "https://www.infoq.cn/feed", "lang": "zh"},
        ],
    },
    {
        "topic": "Writing & Research Tools",
        "category": "writing",
        "description": "Writing techniques, information organization, research tools",
        "rss_sources": [
            {"name": "FlowingData", "url": "https://flowingdata.com/feed/", "lang": "en"},
            {"name": "Sspai", "url": "https://sspai.com/feed", "lang": "zh"},
            {"name": "Writer's Digest", "url": "https://www.writersdigest.com/feed/", "lang": "en"},
        ],
    },
    {
        "topic": "Engineering & Materials Science",
        "category": "engineering",
        "description": "Materials science, engineering inspection, casting technology",
        "rss_sources": [
            {
                "name": "Acta Materialia",
                "url": "https://rss.sciencedirect.com/publication/science/13596454",
                "lang": "en",
            },
            {"name": "Nature Materials", "url": "https://www.nature.com/nmat.rss", "lang": "en"},
            {
                "name": "Engineering Failure Analysis",
                "url": "https://rss.sciencedirect.com/publication/science/13506307",
                "lang": "en",
            },
        ],
    },
]


# ═══════════════════════════════════════════════════
# Configuration Management
# ═══════════════════════════════════════════════════

CONFIG_DIR = Path(__file__).parent.parent / "data"
RSS_TOPICS_PATH = CONFIG_DIR / "rss_topics.json"


def _ensure_rss_config():
    """Ensure the RSS learning topics config file exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not RSS_TOPICS_PATH.exists():
        with open(RSS_TOPICS_PATH, "w", encoding="utf-8") as f:
            json.dump({"topics": DEFAULT_RSS_TOPICS, "version": "1.0"}, f, ensure_ascii=False, indent=2)
        logger.info("Created default RSS learning topics config: %s", RSS_TOPICS_PATH)


def load_rss_topics() -> list[dict]:
    """Load RSS learning topics configuration."""
    _ensure_rss_config()
    with open(RSS_TOPICS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("topics", DEFAULT_RSS_TOPICS)


def save_rss_topics(topics: list[dict]):
    """Save RSS learning topics configuration."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(RSS_TOPICS_PATH, "w", encoding="utf-8") as f:
        json.dump({"topics": topics, "version": "1.0"}, f, ensure_ascii=False, indent=2)
    logger.info("RSS learning topics config updated: %d topics", len(topics))


# ═══════════════════════════════════════════════════
# RSS Fetching
# ═══════════════════════════════════════════════════

_USER_AGENT = "Opprime AutoLearner/1.0 (+https://github.com/opprime)"


def _fetch_feed(url: str, timeout: int = 15) -> str:
    """Fetch an RSS feed, returning raw XML text."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        # Attempt to auto-detect encoding
        content_type = resp.headers.get("Content-Type", "")
        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=")[-1].split(";")[0].strip()
        return raw.decode(encoding, errors="replace")


def _parse_rss(xml_text: str, source_name: str = "") -> list[RssItem]:
    """Parse RSS XML, extracting article list.

    Supports both RSS 2.0 (<item>) and Atom (<entry>) formats.
    """
    items = []

    # Sometimes XML responses come via frontend scripts containing JSON instead of XML
    xml_text = xml_text.strip()
    if not xml_text.startswith("<"):
        logger.warning("Non-XML response (source=%s), skipping parse", source_name)
        return items

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("XML parse failed (source=%s): %s", source_name, e)
        return items

    # Namespace handling

    # Try standard RSS 2.0
    for item_elem in root.iter("item"):
        title = ""
        link = ""
        description = ""
        pub_date = ""

        title_elem = item_elem.find("title")
        if title_elem is not None and title_elem.text:
            title = title_elem.text.strip()

        link_elem = item_elem.find("link")
        if link_elem is not None and link_elem.text:
            link = link_elem.text.strip()

        desc_elem = item_elem.find("description")
        if desc_elem is not None and desc_elem.text:
            description = desc_elem.text.strip()

        date_elem = item_elem.find("pubDate")
        if date_elem is not None and date_elem.text:
            pub_date = date_elem.text.strip()

        if title or link:
            items.append(
                RssItem(
                    title=title,
                    link=link,
                    description=description[:500],  # Truncate long summaries
                    pub_date=pub_date,
                    source_name=source_name,
                )
            )

    # If not RSS 2.0 and no items found, try Atom
    if not items:
        for entry_elem in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = ""
            link = ""
            description = ""
            pub_date = ""

            title_elem = entry_elem.find("{http://www.w3.org/2005/Atom}title")
            if title_elem is not None and title_elem.text:
                title = title_elem.text.strip()

            link_elem = entry_elem.find("{http://www.w3.org/2005/Atom}link")
            if link_elem is not None:
                link = link_elem.get("href", "")

            summary_elem = entry_elem.find("{http://www.w3.org/2005/Atom}summary")
            if summary_elem is not None and summary_elem.text:
                description = summary_elem.text.strip()

            updated_elem = entry_elem.find("{http://www.w3.org/2005/Atom}updated")
            if updated_elem is not None and updated_elem.text:
                pub_date = updated_elem.text.strip()

            if title or link:
                items.append(
                    RssItem(
                        title=title,
                        link=link,
                        description=description[:500],
                        pub_date=pub_date,
                        source_name=source_name,
                    )
                )

    return items


async def fetch_source(source: dict) -> RssFeed:
    """Asynchronously fetch and parse a single RSS source."""
    name = source.get("name", "Unknown")
    url = source.get("url", "")

    feed = RssFeed(source_name=name, rss_url=url)

    if not url:
        feed.error = "No RSS URL"
        return feed

    # Wrap synchronous urllib call in thread pool
    text = ""
    try:
        text = await asyncio.get_event_loop().run_in_executor(None, _fetch_feed, url, 15)
    except urllib.error.HTTPError as e:
        feed.error = f"HTTP {e.code}"
        logger.warning("RSS %s HTTP %d: %s", name, e.code, url)
        return feed
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        feed.error = f"Connection failed: {e}"
        logger.warning("RSS %s connection failed: %s", name, e)
        return feed
    except Exception as e:
        feed.error = f"Error: {e}"
        logger.warning("RSS %s error: %s", name, e)
        return feed

    items = _parse_rss(text, source_name=name)
    feed.items = items
    logger.info("RSS %s: fetched %d articles", name, len(items))
    return feed


async def fetch_topic(topic: dict, max_items_per_source: int = 5) -> dict:
    """Fetch all RSS sources under a learning topic.

    Returns:
        Formatted learning content text, ready to feed to LLM.
    """
    topic_name = topic.get("topic", "Unknown")
    sources = topic.get("rss_sources", [])

    # Fetch all sources in parallel
    tasks = [fetch_source(s) for s in sources]
    feeds = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles = []
    errors = []

    for i, feed in enumerate(feeds):
        if isinstance(feed, Exception):
            source_name = sources[i].get("name", "Unknown")
            errors.append(f"{source_name}: {feed}")
            continue

        if feed.error:
            errors.append(f"{feed.source_name}: {feed.error}")

        # Take latest N articles
        items = feed.items[:max_items_per_source]
        for item in items:
            all_articles.append(item)

    # Assemble learning content
    if not all_articles:
        lines = [f"📡 {topic_name} Auto-Learning"]
        if errors:
            lines.append("")
            lines.append("⚠️ Fetch issues:")
            for e in errors:
                lines.append(f"  - {e}")
        lines.append("")
        lines.append("No new articles fetched.")
        return {
            "topic": topic_name,
            "article_count": 0,
            "error_count": len(errors),
            "errors": errors,
            "content": "\n".join(lines),
        }

    lines = [f"📡 {topic_name} Auto-Learning ({len(all_articles)} articles total)"]
    lines.append("")

    if errors:
        lines.append("⚠️ Some sources failed to fetch:")
        for e in errors:
            lines.append(f"  - {e}")
        lines.append("")

    for item in all_articles:
        lines.append(f"## [{item.source_name}] {item.title}")
        lines.append(f"Link: {item.link}")
        if item.description:
            # Clean HTML tags
            desc = item.description
            desc = desc.replace("<p>", "").replace("</p>", "\n")
            desc = desc.replace("<br>", "\n").replace("<br/>", "\n")
            desc = desc.replace("<br />", "\n")
            # Truncate overly long descriptions
            max_desc = 400
            if len(desc) > max_desc:
                desc = desc[:max_desc] + "..."
            lines.append(desc)
        lines.append("")

    return {
        "topic": topic_name,
        "article_count": len(all_articles),
        "error_count": len(errors),
        "errors": errors,
        "content": "\n".join(lines),
    }
