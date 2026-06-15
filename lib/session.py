# SPDX-License-Identifier: MIT
"""
Gbase session manager module

Session Manager: append-only JSONL implementation.
永不物理Delete旧条目，通过Compression路标跳转。

Three-layer context compression (simplified from Claude Code's 5-layer)：
- L1: Online real-time compression — LLM summaries when threshold exceeded
- L2: Multi-layer summary evolution — compactions merged into higher-level summaries
- L3: Session state tracking — dynamic thresholds + context usage stats
"""

import contextlib
import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
_compress_lock = threading.Lock()  # Compression竞态锁，防止守护线程与在线Compression同时跑


class JsonlSessionManager:
    """Append-only JSONL Session管理器，带三层Compression能力。"""

    def __init__(self, filepath: str, max_context: int = 20):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.max_context = max_context
        self._adaptive_max = max_context  # L3: 动态Threshold调节
        self.fh: object | None = None
        self._stats = {"messages": 0, "compactions": 0, "tokens_estimate": 0}
        self._compacted_up_to = 0  # Compression路标
        self._compaction_level = 0  # L2: 当前Summary层级（第几次合并Compression）
        self._open()

    def _open(self):
        """打开或Create JSONL File。"""
        if self.fh:
            try:
                if hasattr(self.fh, "close"):
                    self.fh.close()
            except Exception:
                pass
        self.fh = open(self.filepath, "a+", encoding="utf-8")
        self._rebuild_stats()

    def _rebuild_stats(self):
        """重新统计消息数和Compression层级。"""
        count = 0
        tokens_est = 0
        self._compacted_up_to = 0
        self._compaction_level = 0
        try:
            self.fh.seek(0)
            for line in self.fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if not isinstance(entry, dict):
                        continue
                    etype = entry.get("type", "")
                    if etype == "compaction":
                        self._compacted_up_to = entry.get("first_kept_entry_id", 0)
                        level = entry.get("level", 0)
                        if level > self._compaction_level:
                            self._compaction_level = level
                    elif etype in ("user", "assistant", "tool_call", "tool_result"):
                        count += 1
                        content = entry.get("content", "") or ""
                        tokens_est += len(content) // 4  # 粗略估算
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        self._stats["messages"] = count
        self._stats["tokens_estimate"] = tokens_est
        # L3: 根据Compression层级调节Threshold
        self._update_adaptive_max()

    def _update_adaptive_max(self):
        """保持充足的上下文用于干活（2026-06-15 审计修复）。
        支持环境变量 GUNDAM_MAX_CONTEXT 自定义，默认 500 条（约 250 轮对话）。
        """
        self._adaptive_max = int(os.environ.get("GUNDAM_MAX_CONTEXT", 500))

    def get_stats(self) -> dict:
        return dict(self._stats)

    def get_compaction_level(self) -> int:
        return self._compaction_level

    def get_adaptive_max(self) -> int:
        return self._adaptive_max

    def append(self, entry: dict) -> int:
        """追加一条记录。entry 是消息字典，必须包含 role 字段。"""
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
        self._stats["tokens_estimate"] += len(content) // 4
        return entry["_id"]

    def append_batch(self, entries: list[dict]):
        """批量追加。"""
        for e in entries:
            self.append(e)

    def append_user_message(self, content: str, extra: dict | None = None) -> int:
        """快捷：追加一条User message。"""
        entry = {"role": "user", "content": content}
        if extra:
            entry.update(extra)
        return self.append(entry)

    def get_or_create(self, session_key: str) -> "JsonlSessionManager":
        return self

    def build_context(self, max_messages: int | None = None) -> list[dict]:
        """构建 LLM messages 上下文。

        三层过滤：
        1. compaction entry Skip旧内容，注入Summary（多层：只有最高层Summary注入）
        2. 去掉 tool_call / tool_result
        3. 按轮Compression + 保留最近 max_messages 轮

        L2 多层Summary：如果有多个 compaction level，
        只有最高层的Summary被注入到上下文。
        """
        if max_messages is None:
            max_messages = self._adaptive_max

        messages: list[dict] = []
        current_assistant_buf: dict | None = None
        skipped_compacted = False
        highest_summary = ""  # L2: 最高层Summary
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

                if not isinstance(entry, dict):
                    continue

                entry_type = entry.get("type", "")

                if entry_type == "compaction":
                    skipped_compacted = True
                    messages.clear()
                    current_assistant_buf = None
                    # L2: 同层覆盖，高层保留
                    level = entry.get("level", 0)
                    summary = entry.get("summary", "")
                    if summary and level >= highest_level:
                        highest_summary = summary
                        highest_level = level
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
                    if "tool_calls" in entry:
                        msg["tool_calls"] = entry["tool_calls"]
                    current_assistant_buf = msg

        except Exception as e:
            logger.warning("build_context Exception: %s", e)

        if current_assistant_buf:
            messages.append(current_assistant_buf)

        # L2: 注入最高层Summary到 messages 开头
        if highest_summary:
            level_label = f"L{highest_level + 1}" if highest_level >= 0 else "L1"
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": f"[SessionSummary - {level_label} Compression前的Conversation history]:\n{highest_summary[:600]}",
                },
            )

        # 按轮Compression
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

        # 保留最近 max_messages 轮
        if len(messages) > max_messages:
            messages = messages[-max_messages:]

        return messages

    def get_compaction_context(self, max_messages: int = 15) -> dict:
        """L2: 获取Compression阶段的高层Summary + 近期轮次。

        企业模式 (2026-06-15): 压缩已禁用，直接返回空结果。
        保留旧逻辑以供兼容，但通过 early return 跳过所有文件解析。
        """
        # 企业模式：压缩禁用，返回空结构体
        return {"summaries": [], "recent": []}

        # ── 以下为旧压缩兼容代码（不再执行） ──
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

                if not isinstance(entry, dict):
                    continue

                etype = entry.get("type", "")
                if etype == "compaction":
                    after_last_compact = False  # 重置
                    s = entry.get("summary", "")
                    if s:
                        summaries.append(
                            {
                                "level": entry.get("level", 0),
                                "summary": s,
                                "ts": entry.get("_ts", 0),
                            }
                        )
                elif after_last_compact or etype in ("user", "assistant"):
                    after_last_compact = True
                    if etype in ("user", "assistant"):
                        recent.append(
                            {
                                "role": entry.get("role", etype),
                                "content": entry.get("content", ""),
                            }
                        )
        except Exception:
            pass

        return {"summaries": summaries, "recent": recent[-max_messages:]}

    def compress_l1(self, compress_fn, threshold: int = 20):
        """L1 在线Compression：把旧轮次Compression为Summary。"""
        if self._stats["messages"] < threshold:
            return None

        try:
            context = self.build_context(max_messages=threshold)
            if not context:
                return None

            summary = compress_fn(context)
            if not summary:
                return None

            first_kept_id = self._find_first_kept_id()
            self._write_compaction(summary, first_kept_id, level=0)
            logger.info("L1 CompressionComplete: %d 条 → %d chars (level=%d)", self._stats["messages"], len(summary), 0)
            return summary
        except Exception as e:
            logger.warning("L1 Compression失败: %s", e)
            return None

    def compress_l2(self, compress_fn):
        """L2 多层Compression：把已有Summary + 最新对话 → 更高级Summary。"""
        try:
            ctx = self.get_compaction_context(max_messages=10)
            if not ctx["summaries"] and len(ctx["recent"]) < 10:
                return None

            # 如果只有底层Summary，不急着升级
            if len(ctx["summaries"]) <= 1 and len(ctx["recent"]) < 20:
                return None

            # 构造合并Compression上下文（所有Summary + 最新对话）
            merge_input = []
            for s in sorted(ctx["summaries"], key=lambda x: x.get("level", 0), reverse=True):
                merge_input.append(f"[L{s.get('level', 0) + 1} Summary]: {s['summary'][:400]}")
            if ctx["recent"]:
                merge_input.append("[最新对话]:")
                for m in ctx["recent"][-5:]:
                    role = m.get("role", "user")
                    content = m.get("content", "")[:200]
                    merge_input.append(f"  {role}: {content}")

            merge_text = "\n".join(merge_input)
            if len(merge_text) < 100:
                return None

            summary = compress_fn([{"role": "user", "content": merge_text}])
            if not summary:
                return None

            new_level = self._compaction_level + 1
            first_kept_id = self._find_first_kept_id()
            self._write_compaction(summary, first_kept_id, level=new_level)
            self._compaction_level = new_level
            self._update_adaptive_max()

            logger.info(
                "L2 多层CompressionComplete: %d 条Summary + %d 轮对话 → L%d Summary (%d chars)",
                len(ctx["summaries"]),
                len(ctx["recent"]),
                new_level + 1,
                len(summary),
            )
            return summary
        except Exception as e:
            logger.warning("L2 多层Compression失败: %s", e)
            return None

    def compress(self, compress_fn, threshold: int = 20):
        """兼容旧接口：自动选择 L1 或 L2。加锁防止竞态。"""
        acquired = _compress_lock.acquire(blocking=False)
        if not acquired:
            logger.info("CompressionSkip：另一个Compression任务正在进行中")
            return None
        try:
            l1_result = self.compress_l1(compress_fn, threshold)
            if l1_result:
                if self._compaction_level >= 1 or self._stats.get("compactions", 0) >= 2:
                    self.compress_l2(compress_fn)
                return l1_result
            return None
        finally:
            _compress_lock.release()

    def _find_first_kept_id(self) -> int:
        """找到第一个未被Compression的 entry ID。"""
        try:
            self.fh.seek(0)
            for line in self.fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    eid = entry.get("_id", 0)
                    if eid > self._compacted_up_to:
                        return eid
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        return 0

    def _write_compaction(self, summary: str, first_kept_entry_id: int, level: int = 0):
        """WriteCompression标记到 JSONL。"""
        compaction_entry = {
            "type": "compaction",
            "level": level,
            "summary": summary[:1200],
            "first_kept_entry_id": first_kept_entry_id,
            "_ts": time.time(),
        }
        self.fh.write(json.dumps(compaction_entry, ensure_ascii=False) + "\n")
        self.fh.flush()
        self._compacted_up_to = first_kept_entry_id
        self._stats["compactions"] += 1

    def start_async_compress(self, compress_fn, interval_sec=600, threshold=25):
        """Startup异步Compression守护线程，每隔 interval_sec 检查一次 session 大小，
        超标则触发Compression。使用 daemon 线程，主进程退出自动销毁。
        内置Retry：崩溃后 60 秒自动Restart守护。"""
        import threading

        def _guard():
            retry_delay = 60
            consecutive_fails = 0
            while True:
                try:
                    time.sleep(interval_sec)
                    self._rebuild_stats()
                    msg_count = self._stats.get("messages", 0)
                    if msg_count < threshold:
                        consecutive_fails = 0
                        continue
                    # 试 L1
                    l1 = self.compress_l1(compress_fn, threshold)
                    if l1 and self._compaction_level >= 1:
                        self.compress_l2(compress_fn)
                    consecutive_fails = 0
                except Exception as _exc:
                    consecutive_fails += 1
                    logger.warning("异步Compression第 %d 次失败: %s，%d秒后Retry", consecutive_fails, _exc, retry_delay)
                    with contextlib.suppress(Exception):
                        self.max_context = 20
                    if consecutive_fails >= 5:
                        logger.error("异步Compression连续 5 次失败，将Wait更长时间Retry")
                        retry_delay = 300
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 600)

        t = threading.Thread(target=_guard, daemon=True, name="async-compress")
        t.start()
        logger.info("异步Compression守护已Startup (间隔=%ds, Threshold=%d条)", interval_sec, threshold)

    def close(self):
        if self.fh:
            with contextlib.suppress(Exception):
                self.fh.close()

    def __del__(self):
        self.close()
