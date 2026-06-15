# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/experience.py

经验层 — 自动提取 + 读取 + 注入。

属于三层沉淀体系的第一层（experience）。

v2.1 — 增加去重逻辑：同一规则在 DEDUP_WINDOW 内不重复记录。
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


# ── 规则提取 ────────────────────────────────────────────

_RULES = [
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

    def __init__(self, storage: store_module.Storage, pending_file: str = ""):
        self.storage = storage
        self._skip_count: dict[str, int] = {}
        import os as _os
        self._pending_file = pending_file or _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "data", "pending_experience.jsonl")

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
        tool_errors_summary: str = "",
        llm_client=None,
    ):
        """存入待处理队列，不立即执行。由 cron 定时批量处理。"""
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
            "tool_errors_summary": tool_errors_summary or "",
            "is_successful_task": not has_api_error and not has_failure and tool_calls_count > 0,
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

        # 噪音过滤：只有深度工作/异常信号才入待处理队列
        if tool_calls_count == 0 and not has_failure and not has_api_error and not rollback_occurred:
            return
        # 写入待处理队列文件，由 cron 批量处理
        import json as _json
        import os as _os
        _os.makedirs(_os.path.dirname(self._pending_file), exist_ok=True)
        with open(self._pending_file, "a") as _f:
            _f.write(_json.dumps(context, ensure_ascii=False) + "\n")

    async def flush(self, llm_client=None):
        """批量处理待处理队列文件中所有经验提取。由 cron 调用。"""
        import json as _json
        import os as _os
        if not _os.path.exists(self._pending_file):
            logger.debug("经验提取（flush）: 无待处理文件")
            return
        contexts = []
        with open(self._pending_file) as _f:
            for _l in _f:
                _l = _l.strip()
                if _l:
                    contexts.append(_json.loads(_l))
        _os.remove(self._pending_file)
        if not contexts:
            return
        logger.info("经验提取（flush）: 批量处理 %d 条上下文", len(contexts))
        for ctx in contexts:
            rule_result = _rule_extract(ctx)
            if rule_result:
                rule_name = rule_result["rule"]
                if _is_duplicate_rule(self.storage, rule_name):
                    self._skip_count[rule_name] = self._skip_count.get(rule_name, 0) + 1
                    continue
                entry = {"type": "lesson", "summary": rule_result["summary"], "context": rule_result["context"], "rule": rule_name, "confidence": rule_result["confidence"]}
                self.storage.write("experience", entry, summary=rule_result["summary"], confidence=rule_result["confidence"], rule=rule_name)
                continue
            if llm_client:
                try:
                    await self._llm_extract(ctx, llm_client)
                except Exception as e:
                    logger.warning("经验提取（flush）LLM失败: %s", e)

    async def _llm_extract(self, context: dict, client):
        prompt = (
            "## 角色：Agent运行经验萃取师\n"
            "你是专门负责从Agent日常运行日志中提炼可复用经验的专业分析师。\n"
            "你的核心价值是把零散的单次运行记录，转化为可指导未来Agent执行、可沉淀积累、可检索复用的标准化经验资产。\n\n"
            "## 核心目标\n"
            "1. 从输入的Agent运行日志中，提取所有具备迁移复用价值的经验、方法、规则、避坑点与优化方案\n"
            "2. 输出严格结构化的JSON，可直接存入经验知识库\n"
            "3. 确保每条经验均可落地执行，而非事实复述或空泛总结\n\n"
            "## 你需要重点关注的信号（按优先级）\n"
            "1. 工具报错 > 为什么错？怎么避？\n"
            "2. 用户纠正行为 > 纠正了什么？正确做法是什么？\n"
            "3. 系统边界 > 哪个工具参数变了？哪个路径不能写？\n"
            "4. 成功模式 > 特定场景下什么做法特别有效？\n\n"
            "## 你完全不记录的内容（直接忽略）\n"
            "- 回复长度（没用）\n"
            "- 工具调用次数（没用）\n"
            "- 正常的程序运行流水、无异常的调试打印\n"
            "- 仅描述\"发生了什么\"，无法提炼出复用方法的客观事实\n\n"
            "## 核心萃取规则\n"
            "### 什么是「有效经验」（必须同时满足）\n"
            "1. 可迁移：不止适用于本次单次任务，可指导未来同类场景\n"
            "2. 可执行：明确给出\"在XX场景下，做XX动作/避开XX操作\"的指引\n"
            "3. 有依据：源自日志中的真实运行结果，而非主观推测\n\n"
            "## 当前对话记录\n"
        )
        # 丰富上下文：加入失败原因、回滚信息、工具报错等
        extra_lines = []
        if context.get("tool_errors_summary"):
            extra_lines.append(f"工具报错摘要: {context['tool_errors_summary']}")
        if context.get("failure_reason") and context["failure_reason"] != "未知原因":
            extra_lines.append(f"失败原因: {context['failure_reason']}")
        if context.get("rollback_occurred"):
            extra_lines.append(f"发生过回滚: {context['rollback_action']}")
        if context.get("is_successful_task"):
            extra_lines.append("任务成功完成")
        if extra_lines:
            prompt += "\n".join(extra_lines) + "\n\n"
        
        prompt += (
            f"用户说: {context['user_message'][:500]}\n"
            f"AI 回复: {context['reply'][:500]}\n\n"
            "## 输出要求\n"
            "如果没有值得记录的经验，只回复: null\n"
            "如果有值得记录的经验，回复以下JSON格式：\n"
            '{"summary": "一句话说清经验核心（例如：写文件前先用check_allowed_paths验证路径白名单）", '
            '"context": "什么场景下发生的", '
            '"category": "最佳实践/避坑指南/异常预案/效率优化中的一种"}'
        )
        try:
            response = await client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.3,
            )
            text = response.choices[0].message.content.strip()
            if text == "null" or not text:
                logger.debug("经验提取（LLM）: 无有价值教训")
                return
            result = json.loads(text)
            if "summary" in result:
                summary = result["summary"][:200]
                # 只有可执行的经验才值得高置信度
                has_action = any(kw in summary for kw in ["先", "用", "不要", "避开", "检查", "确认", "改为", "调用"])
                confidence = "high" if has_action and context.get("is_successful_task", False) else "medium"
                entry = {
                    "type": "lesson",
                    "summary": summary,
                    "context": result.get("context", context["user_message"][:200]),
                    "category": result.get("category", ""),
                    "confidence": confidence,
                }
                self.storage.write("experience", entry, summary=summary, confidence=confidence)
                # --- 同步写入鉴面 ---
                try:
                    from tools.mirror_tool import get_mirror_instance

                    m = get_mirror_instance()
                    if m:
                        m.record(
                            content=summary[:200],
                            mtype="lesson",
                            tags=["experience", "llm"],
                            source="experience:llm",
                        )
                except Exception:
                    pass
                logger.info("经验提取（LLM）: %s (confidence=%s, category=%s)", summary, confidence, entry.get("category",""))
        except Exception as e:
            logger.debug("经验提取（LLM） 异常: %s", e)

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
