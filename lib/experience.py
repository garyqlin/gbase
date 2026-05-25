# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/experience.py

Experience layer — auto-extract + read + inject.

属于三层沉淀体系的第一层（experience）。

v2.1 — 增加去重逻辑：同一规则在 DEDUP_WINDOW 内不重复记录。
"""

import json
import logging

from . import storage as store_module  # type: ignore[import]

logger = logging.getLogger(__name__)

_MAX_INJECTION = 5
"""每次注入到 system prompt 的经验条数。"""

_RECENT_DEDUP_WINDOW = 10
"""去重窗口：检查最近 N 条经验，同 rule 名出现 ≥2 次则跳过。"""

_RECENT_DEDUP_MIN_COUNT = 2
"""去重阈值：窗口内同规则出现次数 ≥ 此值则跳过。"""


# ── 规则提取 ────────────────────────────────────────────

_RULES = [
    {
        "name": "tool_excessive",
        "check": lambda ctx: ctx.get("tool_calls_count", 0) > 5,
        "summary": "此次任务工具调用次数偏多（{tool_calls_count}次），下次同类任务应该先规划再调工具",
        "confidence": "medium",
    },
    {
        "name": "short_reply",
        "check": lambda ctx: len(ctx.get("reply", "")) < 80,
        "summary": "回复长度偏短（{reply_len}字），下次应尽量提供更完整的回答",
        "confidence": "low",
    },
    {
        "name": "api_error",
        "check": lambda ctx: ctx.get("has_api_error", False),
        "summary": "工具调用时有 API 错误，下次应注意检查工具是否可用",
        "confidence": "high",
    },
]


def _rule_extract(context: dict) -> dict | None:
    """用规则提取经验。命中最优先的规则则返回，否则 None。"""
    for rule in _RULES:
        if rule["check"](context):
            summary = rule["summary"].format(**context)
            return {
                "type": "lesson",
                "summary": summary,
                "context": context.get("user_message", "")[:200],
                "rule": rule["name"],
                "confidence": rule["confidence"],
            }
    return None


def _is_duplicate_rule(storage: "store_module.Storage", rule_name: str) -> bool:
    """检查最近经验中同规则是否已过多。

    读取最近 _RECENT_DEDUP_WINDOW 条经验，统计同 rule_name 的出现次数。
    如果 ≥ _RECENT_DEDUP_MIN_COUNT，视为重复噪音，返回 True。
    """
    try:
        recent = storage.read_recent("experience", limit=_RECENT_DEDUP_WINDOW)
        if not recent:
            return False
        count = sum(1 for r in recent if r.get("rule") == rule_name or (rule_name in r.get("summary", "")))
        return count >= _RECENT_DEDUP_MIN_COUNT
    except Exception as e:
        logger.debug("去重检查异常: %s", e)
        return False


# ── 经验提取器 ──────────────────────────────────────────


class ExperienceEngine:
    """经验引擎。绑定到一个 Storage 实例上运作。"""

    def __init__(self, storage: store_module.Storage):
        self.storage = storage
        self._pending_extract: list[dict] = []
        self._skip_count: dict[str, int] = {}

    async def extract(
        self, user_message: str, reply: str, tool_calls_count: int = 0, has_api_error: bool = False, llm_client=None
    ):
        """从一次对话中提取经验。先跑规则 → 去重 → 写库。"""
        context = {
            "user_message": user_message,
            "reply": reply,
            "reply_len": len(reply),
            "tool_calls_count": tool_calls_count,
            "has_api_error": has_api_error,
        }

        rule_result = _rule_extract(context)
        if rule_result:
            rule_name = rule_result["rule"]

            if _is_duplicate_rule(self.storage, rule_name):
                self._skip_count[rule_name] = self._skip_count.get(rule_name, 0) + 1
                logger.debug("经验去重跳过: rule=%s (已跳过%d次)", rule_name, self._skip_count[rule_name])
                return

            logger.info("经验提取（规则）: %s", rule_result["summary"][:60])
            # --- 如果是成功完成任务自动刻入 insight ---
            if tool_calls_count > 0 and not has_api_error and rule_result["type"] != "insight":
                _record_success_insight(self, user_message, tool_calls_count)
            entry = {
                "type": rule_result["type"],
                "summary": rule_result["summary"],
                "context": rule_result["context"],
                "rule": rule_name,
                "confidence": rule_result["confidence"],
            }
            self.storage.write(
                "experience",
                entry,
                summary=rule_result["summary"],
                confidence=rule_result["confidence"],
                rule=rule_name,
            )
            # --- 同步写入鉴面 ---
            try:
                from tools.mirror_tool import get_mirror_instance

                m = get_mirror_instance()
                if m:
                    m.record(
                        content=rule_result["summary"][:200],
                        mtype="lesson",
                        tags=["experience", rule_name],
                        source="experience:rule",
                    )
            except Exception:
                pass

            return

        if llm_client:
            try:
                await self._llm_extract(context, llm_client)
            except Exception as e:
                logger.warning("经验提取（LLM）失败: %s", e)

    async def _llm_extract(self, context: dict, client):
        prompt = (
            "从一次对话中提取 0-1 条有价值的经验教训。\n\n"
            f"用户说: {context['user_message'][:300]}\n"
            f"AI 回复: {context['reply'][:300]}\n"
            f"工具调用: {context['tool_calls_count']} 次\n"
            f"API 错误: {context['has_api_error']}\n\n"
            "如果没有什么值得记住的教训，只回复: null\n"
            "如果有一条值得记住的教训，回复 JSON: "
            '{"summary": "一句话教训", "context": "背景描述"}'
        )
        try:
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.3,
            )
            text = response.choices[0].message.content.strip()
            if text == "null" or not text:
                logger.debug("经验提取（LLM）: 无有价值教训")
                return
            result = json.loads(text)
            if "summary" in result:
                entry = {
                    "type": "lesson",
                    "summary": result["summary"][:200],
                    "context": result.get("context", context["user_message"][:200]),
                    "confidence": "low",
                }
                self.storage.write("experience", entry, summary=result["summary"][:200], confidence="low")
                # --- 同步写入鉴面 ---
                try:
                    from tools.mirror_tool import get_mirror_instance

                    m = get_mirror_instance()
                    if m:
                        m.record(
                            content=result["summary"][:200],
                            mtype="lesson",
                            tags=["experience", "llm"],
                            source="experience:llm",
                        )
                except Exception:
                    pass

                logger.info("经验提取（LLM）: %s", result["summary"][:60])

                # --- 自动刻入 insight（成功任务不留空洞） ---
                tc = context.get("tool_calls_count", 0)
                he = context.get("has_api_error", True)
                if tc > 0 and not he:
                    _record_success_insight(self, context.get("user_message", ""), tc)
        except Exception as e:
            logger.debug("经验提取（LLM） 异常: %s", e)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """搜索经验库。按 summary 模糊匹配。"""
        try:
            conn = None
            if hasattr(self.storage, "db_path") and self.storage.db_path:
                import sqlite3

                conn = sqlite3.connect(self.storage.db_path)
            elif hasattr(self.storage, "_conn") and self.storage._conn:
                conn = self.storage._conn
            if conn is None:
                return self.storage.read_recent("experience", limit=limit)

            rows = conn.execute(
                "SELECT id, summary, content, confidence, hits, created_at FROM entries "
                "WHERE type='experience' AND (summary LIKE ? OR content LIKE ?) "
                "ORDER BY hits DESC, created_at DESC LIMIT ?",
                [f"%{query}%", f"%{query}%", limit],
            ).fetchall()
            results = []
            for r in rows:
                results.append(
                    {
                        "id": r[0],
                        "summary": r[1],
                        "content": r[2],
                        "confidence": r[3],
                        "hits": r[4],
                        "created_at": r[5],
                    }
                )
            if conn and conn != getattr(self.storage, "_conn", None):
                conn.close()
            return results
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("experience.search 失败: %s", e)
            return []

    def forget_by_tags(self, tags_pattern: str = "") -> int:
        """批量删除匹配关键词的经验记录。"""
        if not tags_pattern:
            return 0
        try:
            conn = None
            if hasattr(self.storage, "db_path") and self.storage.db_path:
                import sqlite3

                conn = sqlite3.connect(self.storage.db_path)
            elif hasattr(self.storage, "_conn") and self.storage._conn:
                conn = self.storage._conn
            if conn is None:
                return 0

            deleted = conn.execute(
                "DELETE FROM entries WHERE type='experience' AND summary LIKE ?", [tags_pattern]
            ).rowcount
            conn.commit()
            if deleted > 0:
                try:
                    from tools.mirror_tool import get_mirror_instance

                    m = get_mirror_instance()
                    if m:
                        m.forget(tags_pattern)
                except Exception:
                    pass
            import logging

            logging.getLogger(__name__).info(
                "experience.forget_by_tags: 删除 %d 条 (pattern=%s)", deleted, tags_pattern
            )
            return deleted
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("experience.forget 失败: %s", e)
            return 0

    def get_injection_text(self) -> str:
        recent = self.storage.read_recent("experience", limit=_MAX_INJECTION)
        if not recent:
            return ""
        lines = []
        for r in recent:
            lines.append(f"- [{r['confidence']}] {r['summary']}")
            if r.get("hits", 0) > 0:
                self.storage.record_hit(r["id"])
        return "\n\n## 📝 经验提醒\n以下是你从之前对话中学到的经验（可能不适用于当前场景）：\n" + "\n".join(lines)

    def get_skip_stats(self) -> dict:
        return dict(self._skip_count)


def _record_success_insight(_engine, user_message: str, tool_calls_count: int):
    """成功完成任务后自动刻入 insight，平衡 lesson 与 insight 比例。"""
    try:
        from tools.mirror_tool import get_mirror_instance

        m = get_mirror_instance()
        if m is None:
            return
        # 从用户消息中提取主题关键词
        topic = user_message[:60].strip()
        insight_text = f"成功完成: {topic}（工具调用 {tool_calls_count} 次）"
        # 避免重复：先查有没有类似的
        existing = m.recall(topic[:20], limit=3)
        for ex in existing:
            if ex.get("type") == "insight" and topic[:20] in ex.get("content", ""):
                return  # 已存在同类 insight，跳过
        m.record(
            content=insight_text,
            mtype="insight",
            tags=["success", "auto"],
            source="experience:success",
            strength=0.8,
        )
        logger.info("自动刻入 insight: %s", insight_text[:50])
    except Exception:
        logger.debug("auto insight 失败（非阻塞）")
