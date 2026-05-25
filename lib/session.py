# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/session.py

Session management: append-only JSONL.
永不物理删除旧条目，通过压缩路标跳转。

来自 V0，保留不动。
"""

import contextlib
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class JsonlSessionManager:
    """Append-only JSONL 会话管理器。"""

    def __init__(self, filepath: str, max_context: int = 20):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.max_context = max_context
        self.fh: object | None = None
        self._stats = {"messages": 0, "compactions": 0}
        self._compacted_up_to = 0  # 压缩路标：这条之前的消息已被压缩
        self._open()

    def _open(self):
        """打开或创建 JSONL 文件。"""
        if self.fh:
            try:
                if hasattr(self.fh, "close"):
                    self.fh.close()
            except Exception:
                pass
        # 追加模式，不做 truncate
        self.fh = open(self.filepath, "a+", encoding="utf-8")
        self._rebuild_stats()

    def _rebuild_stats(self):
        """重新统计消息数（从文件开头扫描）。"""
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
        """追加一条记录。entry 是消息字典，必须包含 role 字段。"""
        entry["_id"] = int(time.time() * 1000)
        entry["_ts"] = time.time()
        # 标准化 role 到 entry type
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
        """批量追加。"""
        for e in entries:
            self.append(e)

    def append_user_message(self, content: str, extra: dict | None = None) -> int:
        """快捷：追加一条用户消息。"""
        entry = {"role": "user", "content": content}
        if extra:
            entry.update(extra)
        return self.append(entry)

    def get_or_create(self, _session_key: str) -> "JsonlSessionManager":
        """按 session key 获取或创建一个 session 文件。"""
        # 这个方法的实际效果是返回 self（每个 instance 是一个文件）
        # 外部用 session_key 生成 filepath 后调用 open()
        return self

    def build_context(self, max_messages: int | None = None) -> list[dict]:
        """构建 LLM messages 上下文。

        过滤策略：
        - 只保留 user 和 assistant（纯文本回复）
        - 去掉 tool_call 和 tool_result
        - 跳过压缩标记之前的内容

        Returns:
            LLM 可用的 messages 列表（不含 system prompt）
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

                # 压缩标记：跳过之前所有内容
                if entry_type == "compaction":
                    skipped_compacted = True
                    messages.clear()
                    continue

                # 如果看到压缩标记后的条目，重置 skipped_compacted
                if skipped_compacted and entry_type in ("user", "assistant"):
                    skipped_compacted = False

                # 只保留 user 和 assistant 纯文本消息
                if entry_type == "user":
                    msg = {"role": "user", "content": entry.get("content", "")}
                    messages.append(msg)
                elif entry_type == "assistant":
                    msg = {"role": "assistant", "content": entry.get("content", "")}
                    # DeepSeek 推理模型: 回传 reasoning_content
                    if "reasoning_content" in entry:
                        msg["reasoning_content"] = entry["reasoning_content"]
                    messages.append(msg)
                # 过滤掉 tool_call 和 tool_result

        except Exception as e:
            logger.warning("build_context 异常: %s", e)

        # 只保留最近 max_messages 轮
        if len(messages) > max_messages:
            messages = messages[-max_messages:]

        return messages

    def compact(self, compress_fn, threshold: int = 20):
        """后台压缩：调用 compress_fn 生成摘要，写入压缩标记。"""
        if self._stats["messages"] < threshold:
            return

        try:
            context = self.build_context(max_messages=threshold)
            if not context:
                return

            # 调用外部压缩函数（由框架的 LLM 实现）
            summary = compress_fn(context)

            # 找到第一条未被压缩的消息 ID
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

            # 写入压缩标记（不删除任何旧条目）
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
            logger.info("压缩完成: %d 条 → 摘要 %d chars", self._stats["messages"], len(summary))

        except Exception as e:
            logger.warning("压缩失败: %s", e)

    def close(self):
        if self.fh:
            with contextlib.suppress(Exception):
                self.fh.close()

    def __del__(self):
        self.close()
