# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/session.py

Session management: append-only JSONL.
Never physically deletes old entries; navigates via compaction markers.

Originates from V0, preserved as-is.
"""

import contextlib
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class JsonlSessionManager:
    """Append-only JSONL session manager."""

    def __init__(self, filepath: str, max_context: int = 20):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.max_context = max_context
        self.fh: object | None = None
        self._stats = {"messages": 0, "compactions": 0}
        self._compacted_up_to = 0  # Compaction marker: messages before this have been compacted
        self._open()

    def _open(self):
        """Open or create a JSONL file."""
        if self.fh:
            try:
                if hasattr(self.fh, "close"):
                    self.fh.close()
            except Exception:
                pass
        # Append mode, no truncation
        self.fh = open(self.filepath, "a+", encoding="utf-8")
        self._rebuild_stats()

    def _rebuild_stats(self):
        """Recount messages (scan from the beginning of the file)."""
        count = 0
        self._compacted_up_to = 0
        try:
            self.fh.seek(0)
            for line in self.fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "compaction":
                        self._compacted_up_to = entry.get("first_kept_entry_id", 0)
                    elif entry.get("type") in ("user", "assistant", "tool_call", "tool_result"):
                        count += 1
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        self._stats["messages"] = count

    def get_stats(self) -> dict:
        return dict(self._stats)

    def append(self, entry: dict) -> int:
        """Append a record. entry is a message dictionary and must include a role field."""
        entry["_id"] = int(time.time() * 1000)
        entry["_ts"] = time.time()
        # Normalize role to entry type
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

    def get_or_create(self, _session_key: str) -> "JsonlSessionManager":
        """Get or create a session file by session key."""
        # This method effectively returns self (each instance is a file).
        # External code generates filepath from session_key, then calls open().
        return self

    def build_context(self, max_messages: int | None = None) -> list[dict]:
        """Build LLM messages context.

        Filtering strategy:
        - Only keep user and assistant (plain-text replies)
        - Remove tool_call and tool_result
        - Skip content before compaction markers

        Returns:
            List of messages usable by an LLM (excluding system prompt).
        """
        if max_messages is None:
            max_messages = self.max_context

        messages: list[dict] = []
        skipped_compacted = False

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
                entry.get("_id", 0)

                # Compaction marker: skip all preceding content
                if entry_type == "compaction":
                    skipped_compacted = True
                    messages.clear()
                    continue

                # After seeing a compaction marker, reset skipped_compacted
                if skipped_compacted and entry_type in ("user", "assistant"):
                    skipped_compacted = False

                # Only keep user and assistant plain-text messages
                if entry_type == "user":
                    msg = {"role": "user", "content": entry.get("content", "")}
                    messages.append(msg)
                elif entry_type == "assistant":
                    msg = {"role": "assistant", "content": entry.get("content", "")}
                    # DeepSeek reasoning model: pass back reasoning_content
                    if "reasoning_content" in entry:
                        msg["reasoning_content"] = entry["reasoning_content"]
                    messages.append(msg)
                # Filter out tool_call and tool_result

        except Exception as e:
            logger.warning("build_context error: %s", e)

        # Only keep the latest max_messages rounds
        if len(messages) > max_messages:
            messages = messages[-max_messages:]

        return messages

    def compact(self, compress_fn, threshold: int = 20):
        """Background compaction: call compress_fn to generate a summary and write a compaction marker."""
        if self._stats["messages"] < threshold:
            return

        try:
            context = self.build_context(max_messages=threshold)
            if not context:
                return

            # Call external compression function (implemented by the framework's LLM)
            summary = compress_fn(context)

            # Find the ID of the first uncompacted message
            first_kept_id = 0
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
                            first_kept_id = eid
                            break
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass

            # Write compaction marker (do not delete any old entries)
            compaction_entry = {
                "type": "compaction",
                "summary": summary,
                "first_kept_entry_id": first_kept_id,
                "_ts": time.time(),
            }
            self.fh.write(json.dumps(compaction_entry, ensure_ascii=False) + "\n")
            self.fh.flush()
            self._compacted_up_to = first_kept_id
            self._stats["compactions"] += 1
            logger.info("Compaction complete: %d messages → summary %d chars", self._stats["messages"], len(summary))

        except Exception as e:
            logger.warning("Compaction failed: %s", e)

    def close(self):
        if self.fh:
            with contextlib.suppress(Exception):
                self.fh.close()

    def __del__(self):
        self.close()
