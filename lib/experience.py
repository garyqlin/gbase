# SPDX-License-Identifier: MIT
"""
gbase/lib/experience.py

经验层 — 自动提取 + 读取 + 注入。

属于三层沉淀体系的第一层（experience）。

v2.2 — 反脆弱元认知升级：
  - _llm_extract 从「简单JSON提取」升级为「结构化元认知反思」
  - 新增 _meta_reflection 框架（Situation→Action→Outcome→Lesson）
  - 新增反脆弱规则：失败不静默，记录"什么条件下该用不同策略"
  - 新增 _ANTI_FRAGILE_RULES 动态规则集
"""

import json
import logging

from . import storage as store_module

logger = logging.getLogger(__name__)

_MAX_INJECTION = 5
"""每次注入到 system prompt 的经验条数。"""

_RECENT_DEDUP_WINDOW = 10
"""去重窗口：检查最近 N 条经验，同 rule 名出现 ≥2 次则跳过。"""

_RECENT_DEDUP_MIN_COUNT = 2
"""去重阈值：窗口内同规则出现次数 ≥ 此值则跳过。"""


# ── 反脆弱动态规则 ───────────────────────────────────────
# 这些规则不是静态检查，而是基于「失败模式 → 改进策略」的映射

_ANTI_FRAGILE_RULES = [
    {
        "name": "tool_excessive",
        "check": lambda ctx: ctx.get("tool_calls_count", 0) > 5,
        "summary": "此次任务工具调用次数偏多（{tool_calls_count}次），下次同类任务应该先规划再调工具",
        "confidence": "medium",
    },

    {
        "name": "api_error",
        "check": lambda ctx: ctx.get("has_api_error", False),
        "summary": "工具调用时有 API 错误，下次应注意检查工具是否可用",
        "confidence": "high",
    },
    # ── 反脆弱: 失败尝试也写入经验，不静默回滚 ──
    {
        "name": "failed_action",
        "check": lambda ctx: bool(ctx.get("has_failure", False)),
        "summary": "之前尝试[{failed_approach}]失败，原因是[{failure_reason}]。将来遇到类似情况避免[{dont_repeat}]。",
        "confidence": "medium",
    },
    {
        "name": "failed_rollback",
        "check": lambda ctx: bool(ctx.get("rollback_occurred", False)),
        "summary": "执行回滚: [{rollback_action}] 验证失败，已回滚。这条路走不通。",
        "confidence": "medium",
    },
    # ── 反脆弱: 成功模式提炼（成功比失败更需要分析）──
    {
        "name": "success_pattern",
        "check": lambda ctx: ctx.get("tool_calls_count", 0) >= 3
        and not ctx.get("has_api_error", False)
        and not ctx.get("has_failure", False),
        "summary": "有效模式: [{task_theme}] 用 {tool_calls_count} 次工具调用完成",
        "confidence": "medium",
    },
]


def _rule_extract(context: dict) -> dict | None:
    """用规则提取经验。命中最优先的规则则返回，否则 None。"""
    for rule in _ANTI_FRAGILE_RULES:
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


# ── 元认知反思模板 ──────────────────────────────────────

_META_REFLECTION_PROMPT = """你是一个元认知反思系统。从一次对话中提取结构化反思。

## 反思框架

按 Situation → Action → Outcome → Lesson 四段式分析：

| 维度 | 说明 |
|------|------|
| Situation | 这次对话的场景是什么？用户想要什么？ |
| Action | 你做了什么？用了哪些工具？顺序如何？ |
| Outcome | 结果如何？哪些做得好？哪些不好？ |
| Lesson | 从中能学到什么？下次遇到类似场景该怎么做？ |

## 输出格式

如果没有什么值得记住的教训，只回复: null

如果有一条值得记住的教训，回复 JSON:
{
  "summary": "一句话教训（50字以内，可执行）",
  "context": "背景描述（100字以内）",
  "situation": "场景描述",
  "action": "采取的行动",
  "outcome": "结果评估",
  "meta_pattern": "元模式归类: 工具使用/沟通策略/代码质量/系统设计/安全考虑/其他",
  "when_to_use": "什么条件下这条经验适用",
  "when_to_ignore": "什么条件下这条经验不适用"
}

## 输入数据

用户说: {user_message}
AI 回复: {reply}
工具调用: {tool_calls_count} 次
API 错误: {has_api_error}
失败记录: {has_failure}
"""


# ── 经验提取器 ──────────────────────────────────────────


class ExperienceEngine:
    """经验引擎。绑定到一个 Storage 实例上运作。"""

    def __init__(self, storage: store_module.Storage):
        self.storage = storage
        self._pending_extract: list[dict] = []
        self._skip_count: dict[str, int] = {}

    async def extract(
        self,
        user_message: str,
        reply: str,
        tool_calls_count: int = 0,
        has_api_error: bool = False,
        has_failure: bool = False,
        failure_reason: str = "",
        failed_approach: str = "",
        dont_repeat: str = "",
        rollback_occurred: bool = False,
        rollback_action: str = "",
        llm_client=None,
    ):
        """从一次对话中提取经验。先跑规则 → 去重 → 写库。"""
        # 提取任务主题（前60字，去标点）
        import re as _re
        task_theme = _re.sub(r"[^\u4e00-\u9fff\w\s]", "", user_message[:60]).strip()

        context = {
            "user_message": user_message,
            "reply": reply,
            "reply_len": len(reply),
            "tool_calls_count": tool_calls_count,
            "has_api_error": has_api_error,
            "has_failure": has_failure,
            "failure_reason": failure_reason or "未知原因",
            "failed_approach": failed_approach or "未知方案",
            "dont_repeat": dont_repeat or failure_reason or "未知",
            "rollback_occurred": rollback_occurred,
            "rollback_action": rollback_action or "",
            "task_theme": task_theme or "未知任务",
            "successful_calls": tool_calls_count,
            "effective_strategy": "标准工具流程",
        }

        # 第一阶段：规则提取（快速通道）
        rule_result = _rule_extract(context)
        if rule_result:
            rule_name = rule_result["rule"]

            if _is_duplicate_rule(self.storage, rule_name):
                self._skip_count[rule_name] = self._skip_count.get(rule_name, 0) + 1
                logger.debug("经验去重跳过: rule=%s (已跳过%d次)", rule_name, self._skip_count[rule_name])
                return

            logger.info("经验提取（规则）: %s", rule_result["summary"][:60])
            # --- 如果是成功完成任务自动刻入 insight ---
            if tool_calls_count > 0 and not has_api_error and rule_result["type"] != "insight" and not has_failure:
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

        # 第二阶段：LLM 元认知反思（深度通道）
        if llm_client:
            try:
                await self._llm_extract(context, llm_client)
            except Exception as e:
                logger.warning("经验提取（LLM）失败: %s", e)

    async def _llm_extract(self, context: dict, client):
        """元认知反思提取 — 从「发生了什么」升级到「为什么发生、如何避免、什么条件下该用不同策略」。

        使用 _META_REFLECTION_PROMPT 模板，按 Situation→Action→Outcome→Lesson 四段式分析。
        """
        prompt = _META_REFLECTION_PROMPT.format(
            user_message=context["user_message"][:300],
            reply=context["reply"][:300],
            tool_calls_count=context["tool_calls_count"],
            has_api_error=context["has_api_error"],
            has_failure=context["has_failure"],
        )
        try:
            response = await client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.3,
            )
            text = response.choices[0].message.content.strip()
            if text == "null" or not text:
                logger.debug("经验提取（LLM）: 无有价值教训")
                return

            # 类型防御：LLM 可能返回不完整 JSON（被截断的末尾）
            is_clean = False
            for try_idx in range(3):
                try:
                    result = json.loads(text)
                    is_clean = True
                    break
                except json.JSONDecodeError:
                    # 尝试找到最晚的完整 JSON 截止点
                    last_brace = text.rfind("}")
                    if last_brace > 0:
                        text = text[:last_brace + 1]
                    else:
                        break
            if not is_clean:
                logger.warning("经验提取（LLM）: JSON 解析失败，跳过")
                return

            if "summary" in result:
                # 构建结构化 entry
                summary = result["summary"][:200]
                content_obj = {
                    "situation": result.get("situation", ""),
                    "action": result.get("action", ""),
                    "outcome": result.get("outcome", ""),
                    "meta_pattern": result.get("meta_pattern", "其他"),
                    "when_to_use": result.get("when_to_use", ""),
                    "when_to_ignore": result.get("when_to_ignore", ""),
                }
                content_json = json.dumps(content_obj, ensure_ascii=False)

                entry = {
                    "type": "lesson",
                    "summary": summary,
                    "content": content_json,
                    "context": result.get("context", context["user_message"][:200]),
                    "confidence": "medium",
                    "meta_pattern": result.get("meta_pattern", "其他"),
                }
                self.storage.write(
                    "experience",
                    entry,
                    summary=summary,
                    confidence="medium",
                )
                # --- 同步写入鉴面 ---
                try:
                    from tools.mirror_tool import get_mirror_instance

                    m = get_mirror_instance()
                    if m:
                        m.record(
                            content=summary,
                            mtype="lesson",
                            tags=["experience", "meta_reflection", result.get("meta_pattern", "other")],
                            source="experience:meta_reflection",
                        )
                except Exception:
                    pass

                logger.info("经验提取（元认知反思）: %s", summary[:60])

                # --- 自动刻入 insight（成功任务不留空洞） ---
                if context.get("tool_calls_count", 0) > 0 and not context.get("has_api_error", False):
                    _record_success_insight(self, context.get("user_message", ""), context["tool_calls_count"])

        except (json.JSONDecodeError, KeyError) as e:
            logger.debug("经验提取（LLM）解析失败: %s | 原始响应: %s", e, text[:200] if 'text' in dir() else "N/A")
        except Exception as e:
            logger.debug("经验提取（LLM）异常: %s", e)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """搜索经验库。优先 FTS5 全文检索，无结果时回退 LIKE 模糊匹配。

        排序逻辑：
        - 先按 BM25 相关性分 + 内容长度惩罚（太长降级）
        - 同分按 hits 降序
        - 最终 limit 条
        """
        try:
            conn = None
            if hasattr(self.storage, "db_path") and self.storage.db_path:
                import sqlite3

                conn = sqlite3.connect(self.storage.db_path)
            elif hasattr(self.storage, "_conn") and self.storage._conn:
                conn = self.storage._conn
            if conn is None:
                return self.storage.read_recent("experience", limit=limit)

            # 检查 FTS5 索引是否存在
            has_fts = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='entries_fts'"
            ).fetchone()

            rows = []
            if has_fts:
                try:
                    # 对中文查询做简单 tokenize：保留原样 + 拆字
                    import re as _re

                    tokens = _re.sub(r"[^\u4e00-\u9fff\w\s]", " ", query).strip()
                    fts_query = " OR ".join(
                        f'"{t}" OR "{t}*"' if len(t) >= 2 else f'"{t}"'
                        for t in tokens.split()
                    ) or f'"{query}"'

                    # FTS5 BM25 排序 + 内容长度惩罚（太长的长篇分析文降级）
                    rows = conn.execute(
                        "SELECT e.id, e.summary, e.content, e.confidence, e.hits, e.created_at "
                        "FROM entries e "
                        "JOIN entries_fts fts ON e.id = fts.rowid "
                        "WHERE e.type='experience' AND entries_fts MATCH ? "
                        "ORDER BY "
                        "  rank + "  # BM25 基线
                        "  CASE WHEN LENGTH(e.summary) > 80 THEN 2.0 ELSE 0.0 END + "  # 长摘要降级
                        "  CASE WHEN LENGTH(e.content) > 600 THEN 3.0 ELSE 0.0 END "  # 长内容降级
                        "LIMIT ?",
                        [fts_query, limit],
                    ).fetchall()
                except Exception as ftse:
                    import logging as _lg

                    _lg.getLogger(__name__).debug("FTS5 搜索失败，回退 LIKE: %s", ftse)

            if not rows:
                # 回退 LIKE 模糊匹配
                rows = conn.execute(
                    "SELECT id, summary, content, confidence, hits, created_at FROM entries "
                    "WHERE type='experience' AND (summary LIKE ? OR content LIKE ?) "
                    "ORDER BY "
                    "  CASE WHEN LENGTH(summary) > 80 THEN 2 ELSE 0 END + "
                    "  CASE WHEN LENGTH(content) > 600 THEN 3 ELSE 0 END, "
                    "hits DESC, created_at DESC LIMIT ?",
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

    # ── GMem: P2 经验标准化（export/import）──

    GMEM_VERSION = "1.0"

    def export(
        self, selector: str = "", limit: int = 500, min_confidence: str = "", tags_filter: list[str] | None = None
    ) -> list[dict]:
        """导出经验为标准 JSON 列表。"""
        _ = tags_filter  # noqa: ARG002 — 保留接口签名
        try:
            conn = None
            if hasattr(self.storage, "db_path") and self.storage.db_path:
                import sqlite3

                conn = sqlite3.connect(self.storage.db_path)
            elif hasattr(self.storage, "_conn") and self.storage._conn:
                conn = self.storage._conn
            if conn is None:
                return self.storage.read_recent("experience", limit=limit)

            where_clauses = ["type='experience'"]
            params: list = []
            if selector:
                where_clauses.append("(summary LIKE ? OR content LIKE ?)")
                params.extend([f"%{selector}%", f"%{selector}%"])
            if min_confidence:
                where_clauses.append("confidence >= CASE ? WHEN 'high' THEN 0.8 WHEN 'medium' THEN 0.5 ELSE 0 END")
                params.append(min_confidence)
            rows = conn.execute(
                f"SELECT id, summary, content, confidence, hits, created_at "
                f"FROM entries WHERE {' AND '.join(where_clauses)} "
                f"ORDER BY hits DESC, created_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            results = []
            for r in rows:
                # tags 和 rule 从 content JSON 中提取（兼容旧表无独立列）
                tags_list = []
                rule_text = ""
                try:
                    content_obj = json.loads(r[2])
                    if isinstance(content_obj, dict):
                        tags_list = content_obj.get("tags", [])
                        rule_text = content_obj.get("rule", "")
                except (json.JSONDecodeError, TypeError):
                    pass
                results.append(
                    {
                        "version": self.GMEM_VERSION,
                        "type": "experience",
                        "summary": r[1],
                        "content": r[2],
                        "confidence": r[3],
                        "hits": r[4],
                        "created_at": r[5],
                        "tags": tags_list,
                        "rule": rule_text,
                    }
                )
            if conn and conn != getattr(self.storage, "_conn", None):
                conn.close()
            return results
        except Exception as e:
            logging.getLogger(__name__).warning("experience.export 失败: %s", e)
            return []

    def import_experiences(self, data: list[dict], overwrite: bool = False, strict_version: bool = False) -> int:
        """批量导入经验，自动去重。

        Args:
            data: JSON 列表
            overwrite: 是否覆盖已有
            strict_version: 严格版本校验
        Returns:
            实际导入条数
        """
        count = 0
        for entry in data:
            if strict_version and entry.get("version") != self.GMEM_VERSION:
                continue
            summary = entry.get("summary", "")
            if not summary:
                continue
            if not overwrite and _is_duplicate_rule(self.storage, entry.get("rule", "") or summary[:30]):
                continue
            self.storage.write(
                "experience",
                {
                    "type": "experience",
                    "summary": summary,
                    "content": entry.get("content", ""),
                    "confidence": entry.get("confidence", "medium"),
                },
                summary=summary,
                confidence=entry.get("confidence", "medium"),
            )
            try:
                from tools.mirror_tool import get_mirror_instance

                m = get_mirror_instance()
                if m:
                    m.record(
                        content=summary[:200],
                        mtype="lesson",
                        tags=entry.get("tags", ["experience", "import"]),
                        source="experience:import",
                        strength=0.7,
                    )
            except Exception:
                pass
            count += 1
        logging.getLogger(__name__).info("experience.import: 成功导入 %d 条", count)
        return count

    @staticmethod
    def _parse_tags(tags_val) -> list[str]:
        if not tags_val:
            return []
        if isinstance(tags_val, str):
            return [t.strip() for t in tags_val.split(",") if t.strip()]
        if isinstance(tags_val, list):
            return tags_val
        return []

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
