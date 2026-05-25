# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/experience.py

Experience layer — auto-extract + read + inject.

Layer 1 of the three-layer sedimentation system — experience.

v2.1 — Added dedup logic: same rule not recorded twice within DEDUP_WINDOW.
"""

import json
import logging

from . import storage as store_module  # type: ignore[import]

logger = logging.getLogger(__name__)

_MAX_INJECTION = 5
"""Number of experience entries injected into system prompt each time."""

_RECENT_DEDUP_WINDOW = 10
"""Dedup window: check the last N experiences; skip if same rule name appears ≥2 times."""

_RECENT_DEDUP_MIN_COUNT = 2
"""Dedup threshold: skip if same rule appears ≥ this many times within the window."""


# ── Rule Extraction ─────────────────────────────────────

_RULES = [
    {
        "name": "tool_excessive",
        "check": lambda ctx: ctx.get("tool_calls_count", 0) > 5,
        "summary": "Too many tool calls in this task ({tool_calls_count}); "
        "next time, plan before calling tools for similar tasks",
        "confidence": "medium",
    },
    {
        "name": "short_reply",
        "check": lambda ctx: len(ctx.get("reply", "")) < 80,
        "summary": "Reply was too short ({reply_len} chars); "
        "try to provide more complete answers next time",
        "confidence": "low",
    },
    {
        "name": "api_error",
        "check": lambda ctx: ctx.get("has_api_error", False),
        "summary": "API error occurred during tool call; check tool availability next time",
        "confidence": "high",
    },
]


def _rule_extract(context: dict) -> dict | None:
    """Extract experience using rules. Return the highest-priority match or None."""
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
    """Check if the same rule has appeared too often recently.

    Read the last _RECENT_DEDUP_WINDOW experiences, count occurrences
    of the same rule_name. If ≥ _RECENT_DEDUP_MIN_COUNT, treat as
    duplicate noise, return True.
    """
    try:
        recent = storage.read_recent("experience", limit=_RECENT_DEDUP_WINDOW)
        if not recent:
            return False
        count = sum(1 for r in recent if r.get("rule") == rule_name or (rule_name in r.get("summary", "")))
        return count >= _RECENT_DEDUP_MIN_COUNT
    except Exception as e:
        logger.debug("Dedup check exception: %s", e)
        return False


# ── Experience Extractor ────────────────────────────────


class ExperienceEngine:
    """Experience engine. Operates on a Storage instance."""

    def __init__(self, storage: store_module.Storage):
        self.storage = storage
        self._pending_extract: list[dict] = []
        self._skip_count: dict[str, int] = {}

    async def extract(
        self, user_message: str, reply: str, tool_calls_count: int = 0, has_api_error: bool = False, llm_client=None
    ):
        """Extract experience from a conversation. Run rules → dedup → write to store."""
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
                logger.debug("Experience dedup skipped: rule=%s (skipped %d times)", rule_name, self._skip_count[rule_name])
                return

            logger.info("Experience extracted (rule): %s", rule_result["summary"][:60])
            # --- Auto-record insight on successful task completion ---
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
            # --- Sync to mirror ---
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
                logger.warning("Experience extraction (LLM) failed: %s", e)

    async def _llm_extract(self, context: dict, client):
        prompt = (
            "Extract 0-1 valuable lessons from a conversation.\n\n"
            f"User said: {context['user_message'][:300]}\n"
            f"AI replied: {context['reply'][:300]}\n"
            f"Tool calls: {context['tool_calls_count']}\n"
            f"API errors: {context['has_api_error']}\n\n"
            "If there is nothing worth remembering, just reply: null\n"
            "If there is one lesson worth remembering, reply with JSON: "
            '{"summary": "one-sentence lesson", "context": "background description"}'
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
                logger.debug("Experience extraction (LLM): no valuable lesson")
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
                # --- Sync to mirror ---
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

                logger.info("Experience extracted (LLM): %s", result["summary"][:60])

                # --- Auto-record insight (successful tasks leave no gaps) ---
                tc = context.get("tool_calls_count", 0)
                he = context.get("has_api_error", True)
                if tc > 0 and not he:
                    _record_success_insight(self, context.get("user_message", ""), tc)
        except Exception as e:
            logger.debug("Experience extraction (LLM) exception: %s", e)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Search experience store by fuzzy-matching summary."""
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

            logging.getLogger(__name__).warning("experience.search failed: %s", e)
            return []

    def forget_by_tags(self, tags_pattern: str = "") -> int:
        """Batch-delete experience records matching keywords."""
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
                "experience.forget_by_tags: deleted %d record(s) (pattern=%s)", deleted, tags_pattern
            )
            return deleted
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("experience.forget failed: %s", e)
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
        return (
            "\n\n## 📝 Experience Reminders\n"
            "The following are lessons learned from previous conversations "
            "(may not apply to the current context):\n"
            + "\n".join(lines)
        )

    def get_skip_stats(self) -> dict:
        return dict(self._skip_count)


def _record_success_insight(_engine, user_message: str, tool_calls_count: int):
    """Auto-record insight after successful task completion to balance lesson/insight ratio."""
    try:
        from tools.mirror_tool import get_mirror_instance

        m = get_mirror_instance()
        if m is None:
            return
        # Extract topic keywords from user message
        topic = user_message[:60].strip()
        insight_text = f"Task completed: {topic} (tool calls: {tool_calls_count})"
        # Avoid duplicates: check for similar first
        existing = m.recall(topic[:20], limit=3)
        for ex in existing:
            if ex.get("type") == "insight" and topic[:20] in ex.get("content", ""):
                return  # Similar insight already exists, skip
        m.record(
            content=insight_text,
            mtype="insight",
            tags=["success", "auto"],
            source="experience:success",
            strength=0.8,
        )
        logger.info("Auto-recorded insight: %s", insight_text[:50])
    except Exception:
        logger.debug("auto insight failed (non-blocking)")
