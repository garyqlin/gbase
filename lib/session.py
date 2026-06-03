# SPDX-License-Identifier: MIT
"""
gbase/lib/session.py

Session management: append-only JSONL implementation.
Never physically delete old entries, navigate via compression markers.

Three-layer context compression system (simplified version of Claude Code's 5-layer compression):
- L1: Real-time online compression - Generate summary with LLM when conversation exceeds threshold
- L2: Multi-layer summary evolution - Merge multiple compactions into higher-level summaries
- L3: Session state tracking - Dynamic compression threshold + context usage statistics
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

class JsonlSessionManager:
    """Append-only JSONL Session Manager with three-layer compression capability."""

    def __init__(self, filepath: str, max_context: int = 100):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.max_context = max_context
        self._adaptive_max = max_context  # L3: Dynamic threshold adjustment
        self.fh: object | None = None
        self._stats = {"messages": 0, "compactions": 0, "tokens_estimate": 0}
        self._compacted_up_to = 0  # Compression marker
        self._compaction_level = 0  # L2: Current summary level (number of merge compressions)
        self._open()

    def _open(self):
        """Open or create JSONL file."""
        if self.fh:
            try:
                if hasattr(self.fh, "close"):
                    self.fh.close()
            except Exception:
                logger.exception("Silent exception")
        self.fh = open(self.filepath, "a+", encoding="utf-8")


    def _update_adaptive_max(self):
        """L3: Dynamically adjust context retention rounds based on compression level."""
        # After each layer of compression, the number of retained rounds decreases, but not below the minimum
        base = self.max_context
        level = self._compaction_level
        if level <= 0:
            self._adaptive_max = base
        elif level == 1:
            self._adaptive_max = max(12, base - 4)
        elif level == 2:
            self._adaptive_max = max(8, base - 8)
        else:
            self._adaptive_max = 50  # Level 3 and above, retain at least 3 rounds (6 messages)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Roughly estimate token count.

        Chinese approx 1.5 chars/token, English approx 4 chars/token, plus safety margin.
        """
        if not text:
            return 0
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars / 4) + 10

    def get_stats(self) -> dict:
        return dict(self._stats)

    def get_compaction_level(self) -> int:
        return self._compaction_level

    def get_adaptive_max(self) -> int:
        return self._adaptive_max

    def append(self, entry: dict) -> int:
        """Append a record. entry is a message dictionary, must contain role field."""
        entry["_id"] = int(time.time() * 1000)
        entry["_ts"] = time.time()
        role = entry.get("role", "unknown")
        if role in ("user", "assistant"):
            entry["type"] = role
        elif role == "tool":
            if entry.get("tool_call_id"):
                entry["type"] = "tool_result"
            else:
                entry["type"] = "tool_call"
        else:
            entry["type"] = role
        self.fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.fh.flush()
        self._stats["messages"] += 1
        content = entry.get("content", "") or ""
        self._stats["tokens_estimate"] += int(self._estimate_tokens(content))
        return entry["_id"]

    def append_batch(self, entries: list[dict]):
        """Batch append."""
        for e in entries:
            self.append(e)

    def append_user_message(self, content: str, extra: dict | None = None) -> int:
        """Shortcut: Append a user message."""
        entry = {"role": "user", "content": content}
        if extra:
            entry.update(extra)
        return self.append(entry)

    def get_or_create(self, session_key: str) -> "JsonlSessionManager":
        return self

    def build_context(self, max_messages: int | None = None, max_tokens: int = 0) -> list[dict]:
        """Build LLM messages context.

        Three-layer filtering：
        1. Compaction entry skips old content, injects summary (multi-layer: only highest level summary is injected)
        2. Remove tool_call / tool_result
        3. Compress by round + retain last max_messages rounds

        If max_tokens > 0, accumulate tokens from back to front, truncate front content when exceeding.

        L2 multi-layer summary: If there are multiple compaction levels,
        Only the highest level summary is injected into the context.
        """
        if max_messages is None:
            max_messages = self._adaptive_max

        messages: list[dict] = []
        current_assistant_buf: dict | None = None
        skipped_compacted = False
        highest_summary = ""  # L2: Highest level summary (for injection)
        highest_entry = None  # L2: Highest level complete entry (for structured field usage)
        highest_level = -1

        try:
            self.fh.seek(0)
            for line in self.fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")

                if entry_type == "compaction":
                    skipped_compacted = True
                    messages.clear()
                    current_assistant_buf = None
                    # L2: 同层覆盖，高层保留 — 读取结构化字段
                    level = entry.get("level", 0)
                    if level >= highest_level:
                        highest_level = level
                        highest_entry = entry
                    continue

                if skipped_compacted and entry_type in ("user", "assistant"):
                    skipped_compacted = False

                if entry_type in ("tool_call", "tool_result"):
                    continue

                if entry_type == "user":
                    if current_assistant_buf is not None:
                        messages.append(current_assistant_buf)
                        current_assistant_buf = None
                    msg = {"role": "user", "content": entry.get("content", "")}
                    messages.append(msg)

                elif entry_type == "assistant":
                    msg = {"role": "assistant", "content": entry.get("content", "")}
                    if "reasoning_content" in entry:
                        msg["reasoning_content"] = entry["reasoning_content"]
                    # 始终保留 assistant 消息（包括 content="" 只有 tool_calls 的情况），
                    # 避免下一轮 LLM 看不到自己刚说过什么而表现为"失忆"。
                    # 删除 tool_calls 字段防止 API 400（tool results 已被跳过）
                    msg.pop("tool_calls", None)
                    current_assistant_buf = msg

        except Exception as e:
            logger.warning("build_context 异常: %s", e)

        if current_assistant_buf:
            messages.append(current_assistant_buf)

        # L2: 注入最高层摘要到 messages 开头（结构化注入）
        if highest_entry:
            level = highest_entry.get("level", 0)
            level_label = f"L{level + 1}" if level >= 0 else "L1"

            # 构建结构化摘要文本：读取 decisions / key_facts / pending / context
            ctx_parts = [f"[会话摘要 - {level_label} 压缩前的对话历史]"]
            decisions = highest_entry.get("decisions", [])
            key_facts = highest_entry.get("key_facts", [])
            pending = highest_entry.get("pending", [])
            summary_ctx = highest_entry.get("context", "") or highest_entry.get("summary", "")

            if decisions:
                ctx_parts.append("已完成决策:")
                for d in decisions[:10]:
                    ctx_parts.append(f"  - {d}")
            if key_facts:
                ctx_parts.append("重要事实/路径/参数:")
                for f in key_facts[:10]:
                    ctx_parts.append(f"  - {f}")
            if pending:
                ctx_parts.append("待办事项:")
                for p in pending[:8]:
                    ctx_parts.append(f"  - {p}")
            if summary_ctx:
                ctx_parts.append(f"对话摘要: {summary_ctx[:1500]}")

            inject_content = "\n".join(ctx_parts)

            messages.insert(0, {
                "role": "system",
                "content": inject_content,
            })
        elif highest_summary:
            # 兼容旧格式：只有纯文本
            messages.insert(0, {
                "role": "system",
                "content": f"[会话摘要 - 压缩前的对话历史]:\n{highest_summary[:2000]}"
            })

        # 按轮压缩
        compressed: list[dict] = []
        i = 0
        while i < len(messages):
            if i + 1 < len(messages) and messages[i]["role"] == "user" and messages[i + 1]["role"] == "assistant":
                compressed.append(messages[i])
                compressed.append(messages[i + 1])
                i += 2
            elif messages[i]["role"] == "user":
                compressed.append(messages[i])
                i += 1
            else:
                compressed.append(messages[i])
                i += 1

        messages = compressed

        # Token 预算截断（从后往前累计，超出则截掉前面的内容）
        if max_tokens > 0 and messages:
            total = 0
            cutoff = 0
            for i in range(len(messages) - 1, -1, -1):
                content = messages[i].get("content", "") or ""
                total += self._estimate_tokens(content) + 5  # 5 tokens overhead per msg
                if total > max_tokens:
                    cutoff = i + 1
                    break
            if cutoff > 0:
                # 强制保留最后 1 轮完整 user+assistant
                keep_last = []
                for m in reversed(messages):
                    keep_last.insert(0, m)
                    if len(keep_last) >= 2 and keep_last[0]["role"] == "user" and keep_last[1]["role"] == "assistant":
                        break
                messages = messages[cutoff:]
                if len(messages) < len(keep_last):
                    messages = keep_last

        # 保留最近 max_messages 轮
        # 但强制保留最后 1 轮完整 user+assistant（防止 LLM 忘记自己刚说过什么）
        if len(messages) > max_messages:
            # 截断前Save最后完整的 user+assistant 对
            keep = []
            for m in reversed(messages):
                keep.insert(0, m)
                if len(keep) >= 2 and keep[0]["role"] == "user" and keep[1]["role"] == "assistant":
                    break
            messages = messages[-max_messages:]
            # 如果截断后最后两条不是完整的 user+assistant，补回 keep
            if len(keep) >= 2 and len(messages) >= 2:
                if not (messages[-2]["role"] == "user" and messages[-1]["role"] == "assistant"):
                    messages = messages[:-len(keep)] + keep
            elif len(keep) >= 2:
                messages = messages + keep

        return messages

    def get_compaction_context(self, max_messages: int = 15) -> list[dict]:
        """L2: 获取压缩阶段的高层摘要 + 近期轮次。

        不同于 build_context（给 LLM 用），这个方法返回：
        - 所有层级的摘要列表（不是只取最高层）
        - 最新 max_messages 轮对话

        用于 L2 多层压缩：把旧摘要 + 近期对话 → 新摘要。
        """
        summaries: list[dict] = []
        recent: list[dict] = []
        after_last_compact = False

        try:
            self.fh.seek(0)
            for line in self.fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = entry.get("type", "")
                if etype == "compaction":
                    after_last_compact = False  # 重置
                    s = entry.get("summary", "") or entry.get("context", "")
                    if s or entry.get("decisions") or entry.get("key_facts"):
                        summaries.append({
                            "level": entry.get("level", 0),
                            "summary": s,
                            "decisions": entry.get("decisions", []),
                            "key_facts": entry.get("key_facts", []),
                            "pending": entry.get("pending", []),
                            "context": entry.get("context", ""),
                            "ts": entry.get("_ts", 0),
                        })
                elif after_last_compact or etype in ("user", "assistant"):
                    after_last_compact = True
                    if etype in ("user", "assistant"):
                        recent.append({
                            "role": entry.get("role", etype),
                            "content": entry.get("content", ""),
                        })
        except Exception:
            logger.exception("Silent exception")

        return {"summaries": summaries, "recent": recent[-max_messages:]}

    def close(self):
        if self.fh:
            try:
                self.fh.close()
            except Exception:
                logger.exception("Silent exception")

    def __del__(self):
        self.close()
