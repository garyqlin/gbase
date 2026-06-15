# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/auto_learn.py

自主学习引擎 — 让 Opprime 能定时、主动去网上学习并沉淀。

设计原则：
- 独立于飞书通道运行，但学完后通过飞书给用户推送学习报告
- 学习方向支持 RSS + 搜索两种模式
- 学习成果自动沉淀到 knowledge 层
- 不干扰正常对话处理
"""

import json
import logging
import os
import time
from pathlib import Path

from lib.rss_fetcher import fetch_topic, load_rss_topics

logger = logging.getLogger(__name__)

# 传统搜索模式的学习方向（兼容旧配置）
DEFAULT_LEARN_TOPICS = [
    {
        "topic": "人工智能行业最新动态",
        "search_queries": [
            "人工智能 AI 2026年5月 最新动态",
            "AI industry news May 2026",
        ],
        "category": "ai_news",
        "description": "跟踪 AI 行业重大新闻、融资、产品发布",
    },
]


# 配置文件路径
CONFIG_DIR = Path(__file__).parent.parent / "data"
TOPICS_PATH = CONFIG_DIR / "learn_topics.json"


def _ensure_config():
    """确保学习方向配置文件存在。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not TOPICS_PATH.exists():
        with open(TOPICS_PATH, "w", encoding="utf-8") as f:
            json.dump({"topics": DEFAULT_LEARN_TOPICS, "version": "1.0"}, f, ensure_ascii=False, indent=2)
        logger.info("已创建默认学习方向配置: %s", TOPICS_PATH)


def load_topics() -> list[dict]:
    """加载搜索模式的学习方向配置。"""
    _ensure_config()
    with open(TOPICS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("topics", DEFAULT_LEARN_TOPICS)


def save_topics(topics: list[dict]):
    """保存搜索模式的学习方向配置。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(TOPICS_PATH, "w", encoding="utf-8") as f:
        json.dump({"topics": topics, "version": "1.0"}, f, ensure_ascii=False, indent=2)
    logger.info("搜索学习方向配置已更新: %d 个方向", len(topics))


# ── 学习结果生成 ──────────────────────────────────────────


def format_learn_result(
    topic_name: str,
    total: int,
    saved: int,
    errors: int,
    elapsed: float,
    highlights: list[str],
    analysis_comment: str = "",
) -> str:
    """生成一次学习任务的报告文本，用于飞书投递。"""
    lines = [
        f"📚 自主学习完成：{topic_name}",
        f"⏱️ 用时 {elapsed:.0f} 秒",
    ]
    if total > 0:
        lines.append(f"📊 获取到 {total} 条信息")
    if saved > 0:
        lines.append(f"✅ 沉淀 {saved} 条知识到 memory")
    if errors > 0:
        lines.append(f"⚠️ {errors} 个错误")
    if highlights:
        lines.append("")
        lines.append("📌 重点摘要：")
        for h in highlights[:3]:
            lines.append(f"- {h}")
    if analysis_comment:
        lines.append("")
        lines.append("🔍 分析师评论：")
        comment = analysis_comment[:600]
        for line in comment.split("\n"):
            lines.append(f"  {line}")
    return "\n".join(lines)


# ── 学习任务执行器 ───────────────────────────────────────


class AutoLearner:
    """自主学习执行器。

    由调度器触发，按配置的学习方向进行：
    RSS 抓取（优先） => LLM 理解+知识沉淀 => 分析师模式（跨源关联=>逻辑链=>观点输出=>沉淀） => 自进化反思 => 飞书报告
    学习不是终点，自进化才是。每次学习完成后，必须产出自进化方案。
    如果没有配置 RSS 源，回退到搜索模式。
    """

    def __init__(self, sender_func=None, kernel_run_func=None):
        """初始化。

        Args:
            sender_func: 投递函数，async (open_id, text) -> None. 不传则静默丢弃。
            kernel_run_func: 内核执行函数，async (message, platform, session) -> str.
                           不传则返回空字符串。session 为 None 时会创建一次性 session。
        """
        self._sender = sender_func
        self._kernel_run = kernel_run_func
        self._owner_id = os.environ.get("OPPRIME_LEARN_OWNER", "")

    def set_owner(self, open_id: str):
        """设置学习报告投递目标。"""
        self._owner_id = open_id

    # ── RSS 模式 ──

    async def _execute_rss_topic(self, topic: dict) -> dict:
        """用 RSS 模式执行一个学习方向。

        完整流程（5步闭环）：
        1. 抓取 RSS 源
        2. LLM 阅读文章，remember_fact 存事实（事实层）
        3. 分析师模式：跨源关联 => 逻辑链分析 => 观点输出 => remember_fact 存分析结论（认知层）
        4. 自进化反思：学到的内容如何用于自进化？写入 evolution-log.md
        5. 飞书报告（含分析师评论）
        """
        start = time.time()

        # 先抓 RSS
        rss_result = await fetch_topic(topic)
        content = rss_result.get("content", "")
        article_count = rss_result.get("article_count", 0)
        error_count = rss_result.get("error_count", 0)
        errors = rss_result.get("errors", [])

        topic_name = topic.get("topic", "未知方向")

        if article_count == 0:
            # 没抓到文章，仍然报告
            elapsed = time.time() - start
            result = {
                "topic": topic_name,
                "total": 0,
                "saved": 0,
                "errors": error_count,
                "elapsed": elapsed,
                "highlights": [f"没有获取到新文章（{', '.join(errors[:2])}）"],
                "analysis_comment": "",
            }
            if self._owner_id and self._sender:
                report = format_learn_result(
                    topic_name,
                    0,
                    0,
                    error_count,
                    elapsed,
                    [f"没有获取到新文章（{', '.join(errors[:2])} 等）"],
                )
                try:
                    await self._sender(self._owner_id, report)
                except Exception as e:
                    logger.error("学习报告投递失败: %s", e)
            return result

        # 构建学习指令：把 RSS 结果喂给 LLM（含分析师模式）
        learn_msg = (
            f"📡 自主学习任务：阅读以下 RSS 文章，完成完整的学习闭环。\n\n"
            f"## 学习方向\n{topic_name}\n\n"
            f"## RSS 来源汇总\n{content}\n\n"
            f"## 学习步骤（共 5 步，请按顺序执行）\n\n"
            f"### 第 1 步：事实沉淀\n"
            f"- 仔细阅读以上 RSS 文章\n"
            f"- 对每篇值得长期记住的文章，使用 remember_fact 存入 memory\n"
            f"  （只存长期有价值的事实，不是一次性新闻。事实用 category='general'）\n\n"
            f"### 第 2 步：分析师模式 — 跨源关联\n"
            f"- 回顾本次抓取的所有文章，寻找它们之间的**内在联系**\n"
            f"- 问自己：哪些文章看似无关，其实在讲同一件事？\n"
            f"- 问自己：哪些文章之间存在因果关系、递进关系、或矛盾冲突？\n\n"
            f"### 第 3 步：分析师模式 — 逻辑链分析\n"
            f"- 把找到的关联点组织成**逻辑链**：A => B => C => 结论\n"
            f'- 例如："技术X出现 => 解决Y问题 => 使得Z成为可能 => 行业趋势是..."\n'
            f"- 逻辑链要有因果递进，不能是并列罗列\n\n"
            f"### 第 4 步：分析师模式 — 观点输出 + 沉淀\n"
            f"- 基于以上分析，输出一个**有观点的分析评论**\n"
            f"- 格式参考：\n"
            f"  ```\n"
            f"  ✳️ 我看到一个清晰的闭环正在形成：\n"
            f"  [信息点A] + [信息点B] => [逻辑链] => [结论/洞察]\n"
            f"  ```\n"
            f"- **必须**：用 remember_fact 把分析结论存入 memory\n"
            f"  - fact 内容写完整的分析评论\n"
            f"  - category 用 'analysis'\n"
            f"  - tags 包含 'analysis,insight,{topic_name}'\n\n"
            f"### 第 5 步：自进化反思\n"
            f"- 问自己问题一：这些学习内容对我的自进化有什么帮助？\n"
            f"- 问自己问题二：如何利用这些学习内容进行自进化？\n"
            f"- 根据以上思考，产生具体的自进化方案，并写入 evolution-log.md\n\n"
            f"## 最终回复要求\n"
            f"在完成以上所有步骤后，回复以下内容（作为飞书报告）：\n"
            f"1. 本次学到了什么（2-3句话摘要）\n"
            f"2. 自进化方案\n"
            f"3. 分析师评论（用 ✳️ 开头）\n\n"
            f"注意：这是自主学习任务，请自动执行，不需要问我意见。\n"
            f"学习不是终点，自进化才是。每次学习都必须产出进化方案。\n"
            f"分析师评论是核心产出——把信息变成认知，把认知沉淀为知识。"
        )

        try:
            reply = await self._kernel_run(
                learn_msg,
                platform="auto_learn",
                session=None,
            )
            saved = 1 if reply and len(reply) > 20 else 0
            highlights = [reply[:120]] if saved else []
            # 从回复中提取分析师评论部分（以 ✳️ 开头的内容）
            analysis_comment = ""
            if reply:
                for line in reply.split("\n"):
                    if line.strip().startswith("✳️"):
                        analysis_comment = line.strip()
                        break
        except Exception as e:
            logger.error("RSS 学习 %s LLM 消化失败: %s", topic_name, e)
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

        # 报告
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
                logger.error("学习报告投递失败: %s", e)

        return result

    # ── 搜索模式（旧模式） ──

    async def execute_learn_topic(self, topic: dict) -> dict:
        """执行一个学习方向（搜索模式）。

        Returns:
            {
                "topic": 方向名称,
                "total": 结果总数,
                "saved": 沉淀数,
                "errors": 错误数,
                "elapsed": 用时(秒),
                "highlights": 高亮信息列表,
                "analysis_comment": 分析师评论,
            }
        """
        start = time.time()
        topic_name = topic.get("topic", "未知方向")
        queries = topic.get("search_queries", [])

        total = 0
        saved = 0
        errors = 0
        highlights = []
        analysis_comment = ""

        for query in queries:
            try:
                learn_msg = (
                    f"📡 自主学习任务：按以下要求完成一次学习。\n\n"
                    f"## 学习方向\n{topic_name}\n\n"
                    f"## 学习步骤（共 5 步，请按顺序执行）\n\n"
                    f"### 第 1 步：搜索\n"
                    f"- 使用 search_web 搜索：{query}\n"
                    f"- 阅读搜索结果，理解核心信息\n\n"
                    f"### 第 2 步：事实沉淀\n"
                    f"- 如果发现值得长期记住的事实，使用 remember_fact 存入 memory\n"
                    f"  （category='general'）\n\n"
                    f"### 第 3 步：分析师模式 — 跨源关联 + 逻辑链\n"
                    f"- 寻找本次搜索到的信息之间的内在联系\n"
                    f"- 组织成逻辑链：A => B => C => 结论\n\n"
                    f"### 第 4 步：分析师模式 — 观点输出 + 沉淀\n"
                    f"- 输出有观点的分析评论（用 ✳️ 开头）\n"
                    f"- 用 remember_fact 把分析结论存入 memory（category='analysis'）\n\n"
                    f"### 第 5 步：自进化反思\n"
                    f"- 问自己：这些内容对我的自进化有什么帮助？\n"
                    f"- 产生具体的自进化方案，写入 evolution-log.md\n\n"
                    f"## 最终回复要求\n"
                    f"1. 本次学到了什么（2-3句话摘要）\n"
                    f"2. 自进化方案\n"
                    f"3. 分析师评论（用 ✳️ 开头）\n\n"
                    f"注意：这是自主学习任务，请自动执行。"
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
                    # 提取分析师评论
                    for line in reply.split("\n"):
                        if line.strip().startswith("✳️"):
                            analysis_comment = line.strip()
                            break
            except Exception as e:
                logger.error("搜索学习 %s 执行失败: %s", topic_name, e)
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

        # 报告
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
                logger.error("学习报告投递失败: %s", e)

        return result

    # ── 全量学习入口 ──

    async def learn_all_topics(self, topic_filter: list[str] | None = None) -> list[dict]:
        """执行所有学习方向。

        Args:
            topic_filter: 如果提供，只学习 topic 名称包含过滤词的方向

        Returns:
            各方向学习结果列表
        """
        # 先加载 RSS 方向
        rss_topics = load_rss_topics()
        # 再加载搜索方向
        search_topics = load_topics()

        all_topics = []
        for t in rss_topics:
            all_topics.append({**t, "_mode": "rss"})
        for t in search_topics:
            all_topics.append({**t, "_mode": "search"})

        results = []

        for topic in all_topics:
            topic_name = topic.get("topic", "未知方向")

            # 过滤
            if topic_filter:
                matched = False
                for keyword in topic_filter:
                    if keyword.lower() in topic_name.lower():
                        matched = True
                        break
                if not matched:
                    continue

            logger.info("开始学习: %s (模式: %s)", topic_name, topic.get("_mode"))

            try:
                if topic.get("_mode") == "rss":
                    result = await self._execute_rss_topic(topic)
                else:
                    result = await self.execute_learn_topic(topic)
                results.append(result)
            except Exception as e:
                logger.error("学习方向 %s 执行异常: %s", topic_name, e)
                results.append(
                    {
                        "topic": topic_name,
                        "total": 0,
                        "saved": 0,
                        "errors": 1,
                        "elapsed": 0,
                        "highlights": [f"异常: {str(e)[:80]}"],
                        "analysis_comment": "",
                    }
                )

        return results
