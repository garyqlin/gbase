# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/rss_fetcher.py

RSS 抓取器 — 从 RSS 源抓取最新文章，解析为结构化数据。

不依赖外部 RSS 库，纯标准库实现（urllib + xml.etree.ElementTree）。
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
# 数据模型
# ═══════════════════════════════════════════════════


@dataclass
class RssItem:
    """一条 RSS 文章。"""

    title: str
    link: str
    description: str = ""
    pub_date: str = ""
    source_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RssFeed:
    """一个 RSS 源的一次抓取结果。"""

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
# 学习方向中的 RSS 源配置
# ═══════════════════════════════════════════════════

DEFAULT_RSS_TOPICS = [
    {
        "topic": "人工智能行业最新动态",
        "category": "ai_news",
        "description": "跟踪 AI 行业重大新闻、产品发布、开源项目",
        "rss_sources": [
            {"name": "Hacker News", "url": "https://hnrss.org/frontpage", "lang": "en"},
            {"name": "ArXiv AI", "url": "http://export.arxiv.org/rss/cs.AI", "lang": "en"},
            {"name": "ArXiv ML", "url": "http://export.arxiv.org/rss/cs.LG", "lang": "en"},
            {"name": "机器之心", "url": "https://jiqizhixin.com/feed", "lang": "zh"},
            {"name": "量子位", "url": "https://www.qbitai.com/feed", "lang": "zh"},
            {"name": "36氪AI", "url": "https://rsshub.app/36kr/motif/ai", "lang": "zh"},
            {"name": "知乎AI日报", "url": "https://rsshub.app/zhihu/hotlist", "lang": "zh"},
            {"name": "PaperWeekly", "url": "https://rsshub.app/paperweekly/zhuanlan", "lang": "zh"},
            {"name": "GitHub Trending", "url": "https://rsshub.app/github/trending/daily", "lang": "en"},
            {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/feed/", "lang": "en"},
            {"name": "The Verge AI", "url": "https://www.theverge.com/rss/index.xml", "lang": "en"},
        ],
    },
    {
        "topic": "前端技术与Web开发",
        "category": "frontend",
        "description": "跟踪前端技术动向、CSS/JS 新特性、设计趋势",
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
        "topic": "写作与研究工具",
        "category": "writing",
        "description": "写作技巧、信息整理、研究工具",
        "rss_sources": [
            {"name": "FlowingData", "url": "https://flowingdata.com/feed/", "lang": "en"},
            {"name": "少数派", "url": "https://sspai.com/feed", "lang": "zh"},
            {"name": "Writer's Digest", "url": "https://www.writersdigest.com/feed/", "lang": "en"},
        ],
    },
    {
        "topic": "工程与材料科学",
        "category": "engineering",
        "description": "材料科学、工程检测、铸造技术",
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
# 配置管理
# ═══════════════════════════════════════════════════

CONFIG_DIR = Path(__file__).parent.parent / "data"
RSS_TOPICS_PATH = CONFIG_DIR / "rss_topics.json"


def _ensure_rss_config():
    """确保 RSS 学习方向配置文件存在。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not RSS_TOPICS_PATH.exists():
        with open(RSS_TOPICS_PATH, "w", encoding="utf-8") as f:
            json.dump({"topics": DEFAULT_RSS_TOPICS, "version": "1.0"}, f, ensure_ascii=False, indent=2)
        logger.info("已创建默认 RSS 学习方向配置: %s", RSS_TOPICS_PATH)


def load_rss_topics() -> list[dict]:
    """加载 RSS 学习方向配置。"""
    _ensure_rss_config()
    with open(RSS_TOPICS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("topics", DEFAULT_RSS_TOPICS)


def save_rss_topics(topics: list[dict]):
    """保存 RSS 学习方向配置。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(RSS_TOPICS_PATH, "w", encoding="utf-8") as f:
        json.dump({"topics": topics, "version": "1.0"}, f, ensure_ascii=False, indent=2)
    logger.info("RSS 学习方向配置已更新: %d 个方向", len(topics))


# ═══════════════════════════════════════════════════
# RSS 抓取
# ═══════════════════════════════════════════════════

_USER_AGENT = "Opprime AutoLearner/1.0 (+https://github.com/opprime)"


def _fetch_feed(url: str, timeout: int = 15) -> str:
    """抓取一个 RSS feed，返回原始 XML 文本。"""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        # 尝试自动检测编码
        content_type = resp.headers.get("Content-Type", "")
        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=")[-1].split(";")[0].strip()
        return raw.decode(encoding, errors="replace")


def _parse_rss(xml_text: str, source_name: str = "") -> list[RssItem]:
    """解析 RSS XML，提取文章列表。

    同时支持 RSS 2.0 (<item>) 和 Atom (<entry>) 格式。
    """
    items = []

    # 有时 XML 会通过前端脚本输出，包含 JSON 而非 XML
    xml_text = xml_text.strip()
    if not xml_text.startswith("<"):
        logger.warning("非 XML 响应（source=%s），跳过解析", source_name)
        return items

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("XML 解析失败（source=%s）: %s", source_name, e)
        return items

    # 命名空间处理

    # 尝试标准 RSS 2.0
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
                    description=description[:500],  # 截断长摘要
                    pub_date=pub_date,
                    source_name=source_name,
                )
            )

    # 如果不是 RSS 2.0 且没找到 item，尝试 Atom
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
    """异步抓取一个 RSS 源并解析。"""
    name = source.get("name", "未知")
    url = source.get("url", "")

    feed = RssFeed(source_name=name, rss_url=url)

    if not url:
        feed.error = "无 RSS URL"
        return feed

    # urllib 同步调用包装到线程池
    text = ""
    try:
        text = await asyncio.get_event_loop().run_in_executor(None, _fetch_feed, url, 15)
    except urllib.error.HTTPError as e:
        feed.error = f"HTTP {e.code}"
        logger.warning("RSS %s HTTP %d: %s", name, e.code, url)
        return feed
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        feed.error = f"连接失败: {e}"
        logger.warning("RSS %s 连接失败: %s", name, e)
        return feed
    except Exception as e:
        feed.error = f"异常: {e}"
        logger.warning("RSS %s 异常: %s", name, e)
        return feed

    items = _parse_rss(text, source_name=name)
    feed.items = items
    logger.info("RSS %s: 抓取到 %d 篇文章", name, len(items))
    return feed


async def fetch_topic(topic: dict, max_items_per_source: int = 5) -> dict:
    """抓取一个学习方向下的所有 RSS 源。

    Returns:
        格式化后的学习内容文本，可直接喂给 LLM。
    """
    topic_name = topic.get("topic", "未知")
    sources = topic.get("rss_sources", [])

    # 并行抓取所有源
    tasks = [fetch_source(s) for s in sources]
    feeds = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles = []
    errors = []

    for i, feed in enumerate(feeds):
        if isinstance(feed, Exception):
            source_name = sources[i].get("name", "未知")
            errors.append(f"{source_name}: {feed}")
            continue

        if feed.error:
            errors.append(f"{feed.source_name}: {feed.error}")

        # 取最新的 N 篇
        items = feed.items[:max_items_per_source]
        for item in items:
            all_articles.append(item)

    # 组装学习内容
    if not all_articles:
        lines = [f"📡 {topic_name} 自主学习"]
        if errors:
            lines.append("")
            lines.append("⚠️ 抓取遇到问题：")
            for e in errors:
                lines.append(f"  - {e}")
        lines.append("")
        lines.append("没有获取到新文章。")
        return {
            "topic": topic_name,
            "article_count": 0,
            "error_count": len(errors),
            "errors": errors,
            "content": "\n".join(lines),
        }

    lines = [f"📡 {topic_name} 自主学习 (共 {len(all_articles)} 篇文章)"]
    lines.append("")

    if errors:
        lines.append("⚠️ 部分源抓取失败：")
        for e in errors:
            lines.append(f"  - {e}")
        lines.append("")

    for item in all_articles:
        lines.append(f"## [{item.source_name}] {item.title}")
        lines.append(f"链接: {item.link}")
        if item.description:
            # 清理 HTML 标签
            desc = item.description
            desc = desc.replace("<p>", "").replace("</p>", "\n")
            desc = desc.replace("<br>", "\n").replace("<br/>", "\n")
            desc = desc.replace("<br />", "\n")
            # 截断过长的描述
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
