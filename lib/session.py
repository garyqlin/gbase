# SPDX-License-Identifier: MIT
"""
Gbase session manager module

Session Manager: append-only JSONL implementation.
Never physically deletes old entries; navigates via compaction markers.

Three-layer context compression (simplified from Claude Code's 5-layer):
- L1: Online real-time compression — LLM summaries when threshold exceeded
- L2: Multi-layer summary evolution — compactions merged into higher-level summaries
- L3: Session state tracking — dynamic thresholds + context usage stats
"""

import contextlib
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
_compress_lock = threading.Lock()  # Prevent guard thread and online compress from running concurrently


class JsonlSessionManager:
    """Append-only JSONL session manager with three-layer compression."""

    def __init__(self, filepath: str, max_context: int = 20):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.max_context = max_context
        self._adaptive_max = max_context  # L3: dynamically adjusted threshold
        self.fh: object | None = None
        self._stats = {"messages": 0, "compactions": 0, "tokens_estimate": 0}
        self._compacted_up_to = 0  # Messages before this entry ID have been compacted
        self._compaction_level = 0  # L2: current summary level (how many merges)
        self._open()

    def _open(self):
        """Open or create the JSONL file."""
        if self.fh:
            try:
                if hasattr(self.fh, "close"):
                    self.fh.close()
            except Exception:
                pass
        self.fh = open(self.filepath, "a+", encoding="utf-8")
        self._rebuild_stats()

    def _rebuild_stats(self):
        """Rebuild stats: recount messages and compaction levels."""
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
                    etype = entry.get("type", "")
                    if etype == "compaction":
                        self._compacted_up_to = entry.get("first_kept_entry_id", 0)
                        level = entry.get("level", 0)
                        if level > self._compaction_level:
                            self._compaction_level = level
                    elif etype in ("user", "assistant", "tool_call", "tool_result"):
                        count += 1
                        content = entry.get("content", "") or ""
                        tokens_est += len(content) // 4  # rough estimate
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        self._stats["messages"] = count
        self._stats["tokens_estimate"] = tokens_est
        # L3: adjust threshold based on compaction level
        self._update_adaptive_max()

    def _update_adaptive_max(self):
        """L3: dynamically adjust max context based on compaction level."""
        # After each compaction, narrow retained rounds but keep a floor
        base = self.max_context
        level = self._compaction_level
        if level <= 0:
            self._adaptive_max = base
        elif level == 1:
            self._adaptive_max = max(12, base - 4)
        elif level == 2:
            self._adaptive_max = max(8, base - 8)
        else:
            self._adaptive_max = 6  # level 3+: at least 3 rounds (6 messages)

    def get_stats(self) -> dict:
        return dict(self._stats)

    def get_compaction_level(self) -> int:
        return self._compaction_level

    def get_adaptive_max(self) -> int:
        return self._adaptive_max

    def append(self, entry: dict) -> int:
        """Append a record. Entry is a message dict, must include 'role' field."""
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
        """Batch append."""
        for e in entries:
            self.append(e)

    def append_user_message(self, content: str, extra: dict | None = None) -> int:
        """Convenience: append a user message."""
        entry = {"role": "user", "content": content}
        if extra:
            entry.update(extra)
        return self.append(entry)

    def get_or_create(self, session_key: str) -> "JsonlSessionManager":
        return self

    def build_context(self, max_messages: int | None = None) -> list[dict]:
        """Build LLM messages context.

        Three-layer filtering:
        1. Compaction entries skip old content, inject summary (highest level only)
        2. Strip tool_call / tool_result entries
        3. Pair rounds, keep only recent max_messages rounds

        L2 multi-layer: when multiple compaction levels exist,
        only the highest-level summary is injected.
        """
        if max_messages is None:
            max_messages = self._adaptive_max

        messages: list[dict] = []
        current_assistant_buf: dict | None = None
        skipped_compacted = False
        highest_summary = ""  # L2: highest-level summary
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
                    # L2: same-level overwrite, higher-level retained
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

        # L2: inject highest-level summary at start of messages
        if highest_summary:
            level_label = f"L{highest_level + 1}" if highest_level >= 0 else "L1"
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": f"[SessionSummary - {level_label} Pre-compression conversation history]:\n{highest_summary[:600]}",
                },
            )

        # Pair messages into rounds
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

        # Keep only recent max_messages rounds
        if len(messages) > max_messages:
            messages = messages[-max_messages:]

        return messages

    def get_compaction_context(self, max_messages: int = 15) -> list[dict]:
        """L2: get compaction-stage summaries + recent rounds.

        Unlike build_context (for LLM consumption), this returns:
        - All-level summaries (not just highest)
        - Latest max_messages rounds

        Used by L2 multi-layer compression: old summaries + recent dialogue -> new summary.
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
                    after_last_compact = False  # reset
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
        """L1 online compression: compact old rounds into a summary."""
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
            logger.info("L1 compression done: %d msgs -> %d chars (level=%d)", self._stats["messages"], len(summary), 0)
            return summary
        except Exception as e:
            logger.warning("L1 compression failed: %s", e)
            return None

    def compress_l2(self, compress_fn):
        """L2 multi-layer compression: merge summaries + recent dialogue into higher-level summary."""
        try:
            ctx = self.get_compaction_context(max_messages=10)
            if not ctx["summaries"] and len(ctx["recent"]) < 10:
                return None

            # Only bottom-level summaries, don't rush upgrade
            if len(ctx["summaries"]) <= 1 and len(ctx["recent"]) < 20:
                return None

            # Build merge context (all summaries + recent dialogue)
            merge_input = []
            for s in sorted(ctx["summaries"], key=lambda x: x.get("level", 0), reverse=True):
                merge_input.append(f"[L{s.get('level', 0) + 1} Summary]: {s['summary'][:400]}")
            if ctx["recent"]:
                merge_input.append("[Recent dialogue]:")
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
                "L2 multi-layer compression done: %d summaries + %d rounds -> L%d summary (%d chars)",
                len(ctx["summaries"]),
                len(ctx["recent"]),
                new_level + 1,
                len(summary),
            )
            return summary
        except Exception as e:
            logger.warning("L2 multi-layer compression failed: %s", e)
            return None

    def compress(self, compress_fn, threshold: int = 20):
        """Compatible old interface: auto-select L1 or L2. Lock to prevent race conditions."""
        acquired = _compress_lock.acquire(blocking=False)
        if not acquired:
            logger.info("Compression skipped: another task is in progress")
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
        """Find the first un-compacted entry ID."""
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
        """Write a compaction marker to JSONL."""
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
        """Start async compression guard thread.

        Checks session size every interval_sec, triggers compression when threshold exceeded.
        Uses daemon thread (auto-dies with main process).
        Built-in retry: restarts guard 60s after crash, exponential backoff.
        """
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
                    # Try L1 first
                    l1 = self.compress_l1(compress_fn, threshold)
                    if l1 and self._compaction_level >= 1:
                        self.compress_l2(compress_fn)
                    consecutive_fails = 0
                except Exception as _exc:
                    consecutive_fails += 1
                    logger.warning("Async compress attempt %d failed: %s, retrying in %ds", consecutive_fails, _exc, retry_delay)
                    with contextlib.suppress(Exception):
                        self.max_context = 20
                    if consecutive_fails >= 5:
                        logger.error("Async compress failed 5 consecutive times, waiting longer before retry")
                        retry_delay = 300
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 600)

        t = threading.Thread(target=_guard, daemon=True, name="async-compress")
        t.start()
        logger.info("Async compression guard started (interval=%ds, threshold=%d msgs)", interval_sec, threshold)

    def close(self):
        if self.fh:
            with contextlib.suppress(Exception):
                self.fh.close()

    def __del__(self):
        self.close()
