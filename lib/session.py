# SPDX-License-Identifier: MIT
"""
gbase/lib/session.py

Session 管理：append-only JSONL 实现。
永不物理删除旧条目，通过压缩路标跳转。

三层上下文压缩体系（Claude Code 五层压缩的简化版）：
- L1: 在线实时压缩 — 对话超过阈值时用 LLM 生成摘要
- L2: 多层摘要进化 — 多个 compaction 合并为更高级摘要
- L3: 会话状态追踪 — 动态压缩阈值 + 上下文使用量统计
"""

import asyncio
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

class JsonlSessionManager:
    """Append-only JSONL 会话管理器，带三层压缩能力。"""

    def __init__(self, filepath: str, max_context: int = 100):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.max_context = max_context
        self._adaptive_max = max_context  # L3: 动态阈值调节
        self.fh: object | None = None
        self._stats = {"messages": 0, "compactions": 0, "tokens_estimate": 0}
        self._compacted_up_to = 0  # 压缩路标
        self._compaction_level = 0  # L2: 当前摘要层级（第几次合并压缩）
        self._open()

    def _open(self):
        """打开或创建 JSONL 文件。"""
        if self.fh:
            try:
                if hasattr(self.fh, "close"):
                    self.fh.close()
            except Exception:
                logger.exception("静默异常")
        self.fh = open(self.filepath, "a+", encoding="utf-8")


    def _update_adaptive_max(self):
        """L3: 根据压缩层级动态调节上下文保留轮次。"""
        # 每层压缩后，保留的轮次缩小，但不低于底线
        base = self.max_context
        level = self._compaction_level
        if level <= 0:
            self._adaptive_max = base
        elif level == 1:
            self._adaptive_max = max(12, base - 4)
        elif level == 2:
            self._adaptive_max = max(8, base - 8)
        else:
            self._adaptive_max = 50  # 第三层及以上，至少保留 3 轮（6 条消息）

    @staticmethod
    def _estimate_tokens(text: str | list | dict) -> int:
        """粗略估算 token 数。支持 string / list[dict] / dict 类型。

        中文约 1.5 chars/token，英文约 4 chars/token，加安全边际。
        """
        if not text:
            return 0
        # 处理多模态消息（list[dict]，含 text/image_url）
        if isinstance(text, list):
            total = 0
            for item in text:
                if isinstance(item, dict):
                    for v in item.values():
                        if isinstance(v, str):
                            total += len(v)
                        elif isinstance(v, dict):
                            total += len(str(v))
                elif isinstance(item, str):
                    total += len(item)
            return int(total * 0.35) + 10
        if isinstance(text, dict):
            flat = str(text)
            return int(len(flat) * 0.35) + 10
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
        self._stats["tokens_estimate"] += int(self._estimate_tokens(content))
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

    def get_or_create(self, session_key: str) -> "JsonlSessionManager":
        return self

    def build_context(self, max_messages: int | None = None, max_tokens: int = 0) -> list[dict]:
        """构建 LLM messages 上下文。

        三层过滤：
        1. compaction entry 跳过旧内容，注入摘要（多层：只有最高层摘要注入）
        2. 去掉 tool_call / tool_result
        3. 按轮压缩 + 保留最近 max_messages 轮

        如果 max_tokens > 0，从后往前累计 token，超出则截断前面的内容。

        L2 多层摘要：如果有多个 compaction level，
        只有最高层的摘要被注入到上下文。
        """
        if max_messages is None:
            max_messages = self._adaptive_max

        messages: list[dict] = []
        current_assistant_buf: dict | None = None
        skipped_compacted = False
        highest_summary = ""  # L2: 最高层摘要（用于注入）
        highest_entry = None  # L2: 最高层完整 entry（结构化字段使用）
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
                    # 过滤 image_url 结构：部分模型（如 DeepSeek）不支持多模态 content
                    _raw_content = entry.get("content", "") or ""
                    if isinstance(_raw_content, list):
                        # list[dict] 格式（含 text/image_url）转纯文本段
                        _text_parts = []
                        for _item in _raw_content:
                            if isinstance(_item, dict):
                                if _item.get("type") == "text":
                                    _text_parts.append(_item.get("text", ""))
                                elif _item.get("type") == "image_url":
                                    _text_parts.append("[图片]")
                                else:
                                    _text_parts.append(str(_item))
                            else:
                                _text_parts.append(str(_item))
                        _raw_content = "\n".join(_text_parts)
                    msg = {"role": "user", "content": _raw_content}
                    messages.append(msg)

                elif entry_type == "assistant":
                    _raw_content = entry.get("content", "") or ""
                    if isinstance(_raw_content, list):
                        _text_parts = []
                        for _item in _raw_content:
                            if isinstance(_item, dict):
                                if _item.get("type") == "text":
                                    _text_parts.append(_item.get("text", ""))
                                elif _item.get("type") == "image_url":
                                    _text_parts.append("[图片]")
                                else:
                                    _text_parts.append(str(_item))
                            else:
                                _text_parts.append(str(_item))
                        _raw_content = "\n".join(_text_parts)
                    msg = {"role": "assistant", "content": _raw_content}
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
            # 截断前保存最后完整的 user+assistant 对
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
                            "content": entry.get("content", "") or "",
                        })
        except Exception:
            logger.exception("静默异常")

        return {"summaries": summaries, "recent": recent[-max_messages:]}

    def compress(
        self,
        compress_fn: callable,
        threshold: int = 15,
    ) -> dict | None:
        """L1 + L2: 同步版上下文压缩（供 asyncio.to_thread 调用）。

        参数:
            compress_fn: 接收消息列表，返回摘要文本的回调
            threshold: 触发 L1 压缩的最小消息轮次（默认 15 轮）

        返回:
            压缩统计信息，超时时返回 None
        """
        # L1: 在线实时压缩 — 对话超过阈值时用 LLM 生成摘要
        try:
            context_data = self.get_compaction_context(threshold)
            recent = context_data.get("recent", [])
            if len(recent) < threshold:
                return None

            # L1: 新摘要
            session_text = json.dumps(recent, ensure_ascii=False)[:5000]
            # 类型防御：传给压缩函数的可能是截断的 JSON 字符串
            # compress_fn 在 kernel 层加了解析恢复逻辑
            summary = compress_fn(session_text)
            if not summary:
                return None

            entry = {
                "type": "compaction",
                "level": 0,
                "summary": summary,
                "decisions": [],
                "key_facts": [],
                "pending": [],
                "context": summary[:500],
                "messages_since_last": len(recent),
                "_ts": int(time.time()),
            }
            self.append(entry)
            self._compacted_up_to = self._stats["messages"]
            self._compaction_level = 0
            self._update_adaptive_max()
            self._stats["compactions"] += 1

            # L2: 多层摘要进化 — 已有 compaction 时合并升级
            summaries = context_data.get("summaries", [])
            old_summaries = [s for s in summaries if s.get("level", 0) < 2]
            if len(old_summaries) >= 2:
                merge_text = json.dumps(old_summaries[-3:], ensure_ascii=False)[:4000]
                merged = compress_fn(merge_text)
                if merged:
                    entry = {
                        "type": "compaction",
                        "level": 2,
                        "summary": merged,
                        "decisions": [],
                        "key_facts": [],
                        "pending": [],
                        "context": merged[:500],
                        "messages_since_last": 0,
                        "_ts": int(time.time()),
                    }
                    self.append(entry)
                    self._compaction_level = 2
                    self._stats["compactions"] += 1

            return self._stats.copy()

        except Exception:
            logger.exception("L1/L2 压缩异常（静默）")
            return None

    def start_async_compress(
        self,
        compress_fn: callable,
        interval_sec: int = 600,
        threshold: int = 25,
    ):
        """P2-3: 启动后台异步压缩守护线程。

        在守护线程中循环调用 compress()，失败后等待 10 分钟重试。

        参数:
            compress_fn: 压缩回调（同步）
            interval_sec: 压缩间隔（秒）
            threshold: 触发压缩的最小消息轮次
        """
        import threading as _threading

        def _worker():
            while True:
                try:
                    self.compress(compress_fn, threshold=threshold)
                except Exception as e:
                    logger.exception("Async compress failed: %s", e)
                time.sleep(interval_sec)

        thread = _threading.Thread(target=_worker, daemon=True)
        thread.start()
        logger.info("后台异步压缩守护已启动 (interval=%ds, threshold=%d)", interval_sec, threshold)

    def close(self):
        if self.fh:
            try:
                self.fh.close()
            except Exception:
                logger.exception("静默异常")

    def __del__(self):
        self.close()
