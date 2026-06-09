# SPDX-License-Identifier: MIT
"""
archive_store.py — Archive context storage (memory architecture v2)

Replaces all compression-related code. Does not compress conversations, retains original full text.

Core ideas:
  - Write full original conversation to SQLite, no summarization
  - Batch writes (flush every 5 rounds) reduces write frequency
  - Filter by session_key index + LIKE Search + BM25 sorting
  - Chinese Search fully relies on LIKE (performance test: 10K entries LIKE query <1ms)

Why not choose FTS5:
  - FTS5 unicode61 tokenizer does not index CJK characters (Chinese is ignored)
  - Custom tokenizer requires compiling C extensions (high maintenance cost)
  - LIKE + session_key index performance test at 100K level < 2ms, fully sufficient

Usage:
  store = ArchiveStore(session_key="user:xxx")
  store.append("user", "your question")
  store.append("assistant", "my answer")
  hits = store.search("query keywords")
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Default configuration ──────────────────────────────────────
_DEFAULT_BATCH_SIZE = 5          # Write to DB every N rounds
_DEFAULT_BM25_THRESHOLD = 1.0    # Return when 1+ keywords are hit
_DEFAULT_SEARCH_TOP_K = 4        # Maximum number of results to return
_MAX_CONTENT_CHARS = 2000        # Single content truncation length
_MAX_ENTRIES_PER_SESSION = 50000 # Single session archive limit
_LOCK = threading.Lock()

# ── M3 sparse attention inspired: Time decay segmentation strategy ──
# Within 7 days (hot zone): Full weight, no decay
# 7-30 days (warm zone): Linear decay from 1.0 to 0.5
# 30+ days (cold zone): Exponential decay, ×0.5 every 30 days
_TIME_DECAY_WARM_HOURS = 168     # 7 * 24
_TIME_DECAY_COLD_HOURS = 720     # 30 * 24

# ── Cosmos 3 inspired: Entity conflict detection configuration ──
_CONFLICT_SENSITIVITY = 0.8      # Conflict determination threshold

# ── Hot cache (LRU)───
_HOT_CACHE_MAX_SIZE = 64         # Cache up to 64 entity queries
_HOT_CACHE_TTL_SEC = 3600         # Cache validity period 1 hour


class ArchiveStore:
    """Session archive storage — No compression, retains full original conversation text."""

    def __init__(
        self,
        session_key: str,
        db_path: str | Path | None = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ):
        self.session_key = session_key
        self.batch_size = batch_size
        self._pending: list[dict] = []
        self._pending_markers: list[tuple] = []  # (session_key, timestamp, marker)
        self._batch_count = 0
        self._last_flush_time = time.time()
        self._turn_count = 0
        self._last_user_prefix = ""
        self._turn_entries: list[int] = []  # 当前轮的 entry id（flush 后用于 marker）
        # ── Hot query cache (M3 inspired: LRU accelerates high-frequency entity queries) ──
        self._hot_cache: OrderedDict = OrderedDict()
        self._hot_cache_max = _HOT_CACHE_MAX_SIZE
        self._hot_cache_ttl = _HOT_CACHE_TTL_SEC

        if db_path is None:
            data_dir = Path(__file__).parent.parent / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "archive.db"
            dat_db = data_dir / "dat.db"
            if not db_path.exists() and dat_db.exists():
                _copy_old_data(str(dat_db), str(db_path))

        self.db_path = str(db_path)
        self.max_entries = _MAX_ENTRIES_PER_SESSION
        self._init_db()

    def _init_db(self):
        """Initialize tables (thread-safe, idempotent)."""
        with _LOCK:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS archive_entries (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        content     TEXT NOT NULL,
                        role        TEXT NOT NULL DEFAULT '',
                        session_key TEXT NOT NULL DEFAULT '',
                        timestamp   REAL NOT NULL DEFAULT 0,
                        priority    INTEGER NOT NULL DEFAULT 0,
                        source_id   TEXT NOT NULL DEFAULT ''
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_skey ON archive_entries(session_key)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_ts ON archive_entries(timestamp)")

                # ── Timeline marker table ──
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS archive_markers (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_key TEXT NOT NULL DEFAULT '',
                        timestamp   REAL NOT NULL DEFAULT 0,
                        marker      TEXT NOT NULL DEFAULT '',
                        entry_from  INTEGER NOT NULL DEFAULT 0,
                        entry_to    INTEGER NOT NULL DEFAULT 0
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_marker_skey ON archive_markers(session_key)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_marker_ts ON archive_markers(timestamp)")
                conn.commit()
            finally:
                conn.close()

    # ── Write ──────────────────────────────────────────

    def append(self, role: str, content: str | list | dict, *, priority: int = 0, source_id: str = ""):
        """Append a conversation record. Automatically generate timeline markers."""
        if not content:
            return

        if isinstance(content, (list, dict)):
            content = json.dumps(content, ensure_ascii=False)
        else:
            content = str(content)

        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "..."

        ts = time.time()

        # 用户消息：记录前缀 + Entity conflict detection（Cosmos 3 启发）
        if role == "user":
            conflict = self._check_conflict(content)
            if conflict:
                conflict_note = f"[⚠️ 与前文记录矛盾] {conflict}"
                self._pending.append({
                    "content": conflict_note,
                    "role": "system",
                    "session_key": self.session_key,
                    "timestamp": time.time(),
                    "priority": 1,
                    "source_id": "conflict_detector",
                })
                logger.info("archive_store 检测到实体矛盾: %s", conflict)
            prefix = content.strip()[:40]
            if prefix:
                self._last_user_prefix = prefix

        self._pending.append({
            "content": content,
            "role": role,
            "session_key": self.session_key,
            "timestamp": ts,
            "priority": priority,
            "source_id": source_id,
        })
        self._batch_count += 1
        self._turn_count += 1

        # 助手回复（user+assistant 成对后）→ 生成时间线标记
        if role == "assistant" and self._last_user_prefix:
            marker = self._build_marker(self._last_user_prefix, str(content[:200]))
            self._pending_markers.append((self.session_key, ts, marker))
            self._last_user_prefix = ""

        now = time.time()
        if self._batch_count >= self.batch_size or (now - self._last_flush_time) > 30:
            self.flush()

    # ── 时间线标记 ────────────────────────────────────

    # 事件类型触发词
    _EVENT_PATTERNS = {
        "讨论": ["吗", "?", "？", "你觉得", "怎么样", "怎么看", "如何"],
        "决定": ["做", "选", "决定", "用", "改", "换", "改为", "选择"],
        "修复": ["修复", "修", "bug", "错误", "报错", "失败", "崩溃", "挂了"],
        "发布": ["发布", "上线", "部署", "发布到", "推送"],
        "设计": ["设计", "方案", "架构", "结构", "规划"],
        "问题": ["什么原因", "为什么", "怎么回事", "会不会", "行不行"],
        "咨询": ["能不能", "怎么", "如何", "我想", "可以"],
        "操作": ["重启", "启动", "停止", "运行", "执行", "创建", "删除", "安装"],
    }

    # 停用词列表（不提取为实体）
    _STOP_WORDS = {"这个", "那个", "什么", "怎么", "哪里", "为什么", "可以", "应该", "已经",
                    "一个", "一些", "这些", "那些", "没有", "不是", "就是", "但是", "然后",
                    "因为", "所以", "如果", "还是", "或者", "不过", "虽然", "而且", "除了",
                    "这样", "那样", "可能", "需要", "之后", "之前", "现在", "晚上"}

    @staticmethod
    def _detect_event_type(text: str) -> str:
        """检测事件类型。"""
        for evt, triggers in ArchiveStore._EVENT_PATTERNS.items():
            for t in triggers:
                if t in text:
                    return evt
        return "聊"

    @staticmethod
    def _extract_entities(text: str) -> list[str]:
        """提取实体名词。纯规则式。

        策略概要：
          1. 提取所有 candidate（引号/英文/版本/中文专名）
          2. 用启发式规则过滤掉口语化/非实体的 candidate
          3. 保留 up to 5 个最相关
        """
        candidates = []
        seen = set()

        def _add(e):
            e = e.strip().rstrip('.!?:;"')
            if not e or len(e) < 2:
                return
            if e in seen:
                return
            L = e.lower()
            if L in ("the", "this", "that", "what", "how", "why", "can",
                     "not", "all", "for", "are", "was", "now", "yes",
                     "has", "got", "get", "did", "had", "but", "you",
                     "one", "two", "way", "use", "set", "new", "old",
                     "any", "see", "say", "get", "its", "via"):
                return
            if e in ArchiveStore._STOP_WORDS:
                return
            # 纯数字
            if e.replace('.','').replace('-','').replace('v','').isdigit():
                return
            # 口语/非实体后缀（单字）
            if len(e) >= 3 and e[-1] in "的是有能会要去了来在和着过吧呢么没用可吗嘛":
                return
            # 口语/非实体后缀（双字）
            _BAD_2 = frozenset(["什么", "怎么", "哪里", "哪个", "哪种", "多久", "多大",
                                "实现", "解决", "完成", "处理", "采用", "使用",
                                "连接", "传入", "上传", "下单", "登录", "注册",
                                "一样", "这么", "那么", "这样", "那样",
                                "参数", "功能", "方式", "方法", "问题"])
            if len(e) >= 4 and e[-2:] in _BAD_2:
                return
            # 中文不能以虚词开头（从英文后缀提取时常见）
            if len(e) >= 2 and e[0] in "的是" :
                return
            seen.add(e)
            candidates.append(e)

        # 全大写缩略词（用 (?<![A-Z]) 代替 \b，避免中文干扰）
        for ac in re.findall(r'(?<![A-Z])[A-Z]{3,8}(?![A-Z])', text):
            _add(ac)
        # 驼峰式技术名
        for t in re.findall(r'(?<![A-Za-z])[A-Z][a-z]{2,}(?:[A-Z][A-Za-z0-9]+)+(?![A-Za-z])', text):
            _add(t)
        for t in re.findall(r'(?<![A-Za-z])[A-Z][a-z]{2,}(?![A-Za-z])', text):
            _add(t)
        # 带点/连字符的技术名：Three.js、box-shadow
        for t in re.findall(r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*[.\-/][A-Za-z][A-Za-z0-9]*(?![A-Za-z0-9])', text):
            _add(t)

        # ── 版本号 ──
        for v in re.findall(r'v?\d+\.\d+(?:\.\d+)?', text):
            _add(v)

        # ── 引号内 ──
        for q in re.findall(r'[""「」『』《》]\s*([^""「」『』《》"]{2,30})\s*[""「」『』《》]', text):
            _add(q.strip())

        # ── 中文专名 ──
        # 冒号前以完整词起始（非在句中截断），取最多5字
        for c in re.findall(r'(?:^|[\s，。；])([\u4e00-\u9fff]{2,5})：', text):
            _add(c)
        # 跟在纯英文字母后面的中文（技术实体，如 session压缩）
        for m in re.finditer(r'\b[A-Za-z]{2,}([一-鿿]{2,6})', text):
            c = m.group(1)
            _add(c)

        # 去重 & 截断
        return candidates[:5]

    def _build_marker(self, user_text: str, assistant_text: str) -> str:
        """生成多维时间线标记：事件类型 · 实体1 · 实体2 · 实体3

        不再用"问题前X字 → 回复前Y字"拼接。
        而是提取：
          - 事件类型（讨论/决定/修复/发布等）
          - 实体名词（项目、技术、人物、文件名等）
          - 两者拼接为简洁标记
        """
        combined = user_text + " " + assistant_text

        event = self._detect_event_type(combined)
        entities = self._extract_entities(combined)

        # 拼接标记：事件类型 · 实体1 · 实体2 · 实体3
        parts = [event]
        for e in entities:
            if len(parts) >= 4:
                break
            parts.append(e)

        return " · ".join(parts)

    # ── Cosmos 3 启发：Entity conflict detection ───────────────────

    @staticmethod
    def _get_subject_entity(text: str) -> str | None:
        """提取一句话中最可能的"主题实体"（被陈述的对象）。"""
        # 《》引用的实体
        for q in re.findall(r'[\u300a\u300b]\s*([^\u300a\u300b]{2,20})\s*[\u300a\u300b]', text):
            return q.strip()
        # 冒号前的中文专名
        for c in re.findall(r'([\u4e00-\u9fff]{2,5})：', text):
            return c.strip()
        # "主题实体是…"句型
        for m in re.findall(r'([\u4e00-\u9fff]{2,6})(?:的(?:生日|电话|地址|公司|爱好|名字|手机号))', text):
            return m
        return None

    def _check_conflict(self, content: str) -> str:
        """Write前检查是否与已有存档中同一实体的矛盾事实冲突。

        做浅层模式匹配：
        - 检测到"xxxx是YYY"句式
        - 查找同 session 近期(7天)存档中是否有同一实体但不同值的记录
        - 有冲突则返回冲突描述字符串，否则返回空字符串
        """
        if not content:
            return ""
        subject = self._get_subject_entity(content)
        if not subject:
            return ""
        # 查询最近7天的同 session 存档
        _recent = time.time() - _TIME_DECAY_WARM_HOURS
        with _LOCK:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute(
                    "SELECT content FROM archive_entries WHERE session_key = ? AND timestamp >= ? ORDER BY timestamp DESC LIMIT 20",
                    (self.session_key, _recent),
                )
                recent_entries = [row[0] for row in cursor.fetchall() if row[0]]
            finally:
                conn.close()
        if not recent_entries:
            # 也检查 pending 队列中的内容
            if not self._pending:
                return ""
            recent_entries = [e["content"] for e in self._pending[-10:] if e.get("content")]
            if not recent_entries:
                return ""
        # 浅层冲突检测：同一实体出现但数值不同
        # 提取当前值
        _val_pattern = re.compile(
            rf'{re.escape(subject)}[：:是有的为]' + r'(.{2,40}?)(?:[。！？!?]|$)'
        )
        current_match = _val_pattern.search(content)
        if not current_match:
            # 再试试"是"句型的变体
            _val2 = re.compile(r'(.{2,30})' + re.escape(subject))
            current_match = _val2.search(content)
        if not current_match:
            return ""
        current_val = current_match.group(1).strip()[:40]
        for old_entry in recent_entries:
            old_match = _val_pattern.search(old_entry)
            if old_match:
                old_val = old_match.group(1).strip()[:40]
                if old_val and current_val and old_val != current_val:
                    # 过滤问句假阳性：当前值含疑问词
                    if any(q in current_val for q in ['？', '?', '几号', '什么', '哪天', '吗？']):
                        continue
                    # 去重：同样冲突不重复标记
                    if old_val + current_val in getattr(self, '_recent_conflicts', set()):
                        continue
                    self._recent_conflicts = getattr(self, '_recent_conflicts', set())
                    self._recent_conflicts.add(old_val + current_val)
                    return f"之前记录的「{subject}」是「{old_val}」，现在是「{current_val}」，请确认是否更新"
        return ""

    def flush(self):
        """批量Write pending 数据和时间线标记。"""
        with _LOCK:
            conn = sqlite3.connect(self.db_path)
            try:
                if self._pending:
                    rows = [(e["content"], e["role"], e["session_key"],
                             e["timestamp"], e["priority"], e["source_id"])
                            for e in self._pending]
                    conn.executemany(
                        "INSERT INTO archive_entries (content, role, session_key, timestamp, priority, source_id) VALUES (?, ?, ?, ?, ?, ?)",
                        rows,
                    )

                if self._pending_markers:
                    conn.executemany(
                        "INSERT INTO archive_markers (session_key, timestamp, marker) VALUES (?, ?, ?)",
                        [(sk, ts, m) for sk, ts, m in self._pending_markers],
                    )

                conn.commit()

                # ── 兜底：单 session 存档条数上限 ──
                # 只清理当前 session_key 的旧数据，不碰其他 session。
                # 超出 max_entries 时删除最旧的 30%，保留 70%。
                # 被删记录保留到 trash jsonl 文件（纯文本可 grep 搜索）。
                if self.max_entries > 0:
                    cursor = conn.execute(
                        "SELECT COUNT(*) FROM archive_entries WHERE session_key = ?",
                        (self.session_key,),
                    )
                    count = cursor.fetchone()[0]
                    if count > self.max_entries:
                        # 砍掉最旧的30%
                        keep = int(count * 0.7)
                        cursor = conn.execute(
                            "SELECT id FROM archive_entries WHERE session_key = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                            (self.session_key, keep - 1),
                        )
                        row = cursor.fetchone()
                        if row:
                            cutoff_id = row[0]
                            # 1️⃣ 先读被删数据 → Write trash 文件（纯文本 JSONL）
                            trash = conn.execute(
                                "SELECT content, role, timestamp, source_id FROM archive_entries WHERE session_key = ? AND id < ?",
                                (self.session_key, cutoff_id),
                            ).fetchall()
                            if trash:
                                _save_trash(self.session_key, trash)
                            # 2️⃣ 再删
                            deleted = conn.execute(
                                "DELETE FROM archive_entries WHERE session_key = ? AND id < ?",
                                (self.session_key, cutoff_id),
                            ).rowcount
                            # 同步清理对应的 marker（按相同比例）
                            cursor = conn.execute(
                                "SELECT id FROM archive_markers WHERE session_key = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                                (self.session_key, int(keep * 0.02) or 1,),
                            )
                            marker_row = cursor.fetchone()
                            if marker_row:
                                marker_cutoff = marker_row[0]
                                conn.execute(
                                    "DELETE FROM archive_markers WHERE session_key = ? AND id < ?",
                                    (self.session_key, marker_cutoff),
                                )
                            conn.commit()
                            logger.info(
                                "📐 archive_store: %s 超限 %d，保留 %d (70%%)，归档 %d 条 -> trash，删除了 %d 条",
                                self.session_key, count, keep, len(trash), deleted,
                            )

                if self._pending:
                    logger.debug("ArchiveStore flush: %d entries", len(self._pending))
                if self._pending_markers:
                    logger.debug("ArchiveStore flush: %d markers", len(self._pending_markers))
            except Exception as e:
                logger.warning("ArchiveStore flush failed: %s", e)
                logger.exception("")
                conn.rollback()
            finally:
                conn.close()

        self._pending.clear()
        self._pending_markers.clear()
        self._batch_count = 0
        self._last_flush_time = time.time()

    # ── Search（语义桥）─────────────────────────────────

    def search(self, query: str, top_k: int = _DEFAULT_SEARCH_TOP_K,
               time_from: float | None = None, time_to: float | None = None) -> list[dict]:
        """Search当期会话中与 query 相关的历史记录。

        支持多维弱线索Search：
          - 关键词匹配（中文2-gram/英文分词）
          - 时间范围过滤（可选）
          - priority 加权

        Args:
            query: 搜索关键词（自动分词为多维关键词）
            top_k: 返回条数上限
            time_from: 起始时间戳（可选）
            time_to: 结束时间戳（可选）

        返回：
          [{content, role, timestamp, priority, score}, ...]
        """
        self.flush()

        keywords = self._extract_keywords(query)
        if not keywords:
            logger.debug("语义桥：query 无可Search关键词")
            return []

        # ── 热度缓存命中（M3 启发：高频实体免扫全表） ──
        now = time.time()
        cache_key = query.strip().lower()[:80]
        if cache_key in self._hot_cache:
            cached_at, cached_result = self._hot_cache[cache_key]
            if now - cached_at < self._hot_cache_ttl:
                self._hot_cache.move_to_end(cache_key)
                logger.debug("ArchiveStore 热度缓存命中: %s", cache_key[:40])
                return cached_result

        # 构建 WHERE 条件
        where_parts = ["session_key = ?"]
        params: list = [self.session_key]

        # 关键词条件
        kw_conditions = " OR ".join(f"content LIKE ? COLLATE NOCASE" for _ in keywords)
        where_parts.append(f"({kw_conditions})")
        params.extend(f"%{k}%" for k in keywords)

        # 时间范围
        if time_from is not None:
            where_parts.append("timestamp >= ?")
            params.append(time_from)
        if time_to is not None:
            where_parts.append("timestamp <= ?")
            params.append(time_to)

        sql = f"""
            SELECT content, role, timestamp, priority, source_id, id
            FROM archive_entries
            WHERE {' AND '.join(where_parts)}
            ORDER BY timestamp DESC
        """

        with _LOCK:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute(sql, params)
                all_rows = cursor.fetchall()
            finally:
                conn.close()

        if not all_rows:
            return []

        # 计算每条记录的命中关键词数（作为粗糙的 BM25 替代）
        scored = []
        for content, role, ts, priority, source_id, eid in all_rows:
            if not content:
                continue
            hits = sum(1 for kw in keywords if kw in content or kw.lower() in content.lower())
            if hits == 0:
                continue

            # 分数 = 命中关键词数 + priority 加权 + 时间衰减（M3 启发分段）
            score = hits + (10 if priority > 0 else 0)
            age_hours = (time.time() - (ts or 0)) / 3600
            if age_hours < _TIME_DECAY_WARM_HOURS:
                pass  # 热区：全权重，不衰减
            elif age_hours < _TIME_DECAY_COLD_HOURS:
                # 温区：线性衰减 1.0 → 0.5
                warm_ratio = (_TIME_DECAY_COLD_HOURS - age_hours) / (_TIME_DECAY_COLD_HOURS - _TIME_DECAY_WARM_HOURS)
                score *= 0.5 + 0.5 * warm_ratio
            else:
                # 冷区：每30天半衰
                extra_months = (age_hours - _TIME_DECAY_COLD_HOURS) / 720.0
                score *= max(0.1, 0.5 ** extra_months)

            if score < _DEFAULT_BM25_THRESHOLD:
                continue

            scored.append({
                "content": content,
                "role": role,
                "timestamp": ts or 0,
                "priority": priority or 0,
                "score": round(score, 2),
            })

        scored.sort(key=lambda r: (-r["priority"], -r["score"]))
        result = scored[:top_k]

        # ── Write缓存 ──
        if result:
            self._hot_cache[cache_key] = (time.time(), result)
            if len(self._hot_cache) > self._hot_cache_max:
                self._hot_cache.popitem(last=False)

        return result

    def _extract_keywords(self, query: str) -> list[str]:
        """从查询中提取关键词。

        对中文：2-gram 滑动切分
        对英文：空格分词
        混合：都做，去重
        """
        if not query or not query.strip():
            return []

        query = query.strip()
        keywords: list[str] = []

        # 中文字符 2-gram
        cn_chars = [c for c in query if "\u4e00" <= c <= "\u9fff"]
        if len(cn_chars) >= 2:
            for i in range(len(cn_chars) - 1):
                bigram = cn_chars[i] + cn_chars[i + 1]
                if bigram not in keywords:
                    keywords.append(bigram)

        # 如果中文词数 >= 4，也加 3-gram 提高精度
        if len(cn_chars) >= 4:
            for i in range(len(cn_chars) - 2):
                trigram = cn_chars[i] + cn_chars[i + 1] + cn_chars[i + 2]
                if trigram not in keywords:
                    keywords.append(trigram)

        # 过滤 2-gram 通用词（减少杂音匹配）
        _COMMON_BIGRAMS = {
            "今天", "明天", "昨天", "晚上", "早上", "中午", "下午",
            "一个", "这个", "那个", "什么", "怎么", "哪里", "为什么",
            "可以", "应该", "已经", "没有", "不是", "就是", "还是",
            "因为", "所以", "如果", "但是", "不过", "虽然", "而且",
            "这样", "那样", "可能", "需要", "之后", "之前", "现在",
            "我们", "他们", "你们", "自己", "一些", "这些", "那些",
            "谢谢", "你好", "请问", "好的", "是的", "知道", "觉得",
            "然后", "或者", "还是", "除了", "不想", "想要", "打算",
            "看到", "听说", "觉得", "告诉", "我的", "你的", "他的",
            "大家", "东西", "时候", "不错", "真的", "非常", "很多",
            "工作", "生活", "事情", "感觉", "方面", "一点", "一定",
            "还有", "因为", "出来",
        }
        keywords = [k for k in keywords if k not in _COMMON_BIGRAMS]

        # 中文字符数量 >= 2 时，也尝试直接把整个中文短语当关键词
        if len(cn_chars) >= 2:
            # 提取连续的中文子串（可能被英文/数字打断）
            cn_segments = re.findall(r"[\u4e00-\u9fff]+", query)
            for seg in cn_segments:
                if len(seg) >= 2 and seg not in keywords:
                    keywords.append(seg)

        # 英文/数字关键词（空格分隔 + 过滤短词）
        en_tokens = [t for t in re.findall(r"[a-zA-Z0-9._+#]+", query) if len(t) >= 2]
        for tok in en_tokens:
            tk = tok.lower()
            if tk not in keywords:
                keywords.append(tk)

        if not keywords:
            return []

        # 去重 + 移除过短的
        return [k for k in keywords if len(k) >= 2]

    # ── 时间线 ──────────────────────────────────────────

    def timeline(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """返回当前会话的对话时间线索引（从新到旧）。

        结果格式：
          [
            {
              "marker": "我想做3D小熊 → 好的我们来做...",
              "timestamp": 1717177777.0,
              "time_str": "2026-06-01 20:55",
              "entry_from": 1,
              "entry_to": 2
            },
            ...
          ]

        marker 由 `user问题前30字 → assistant回复前25字` 拼接。
        """
        self.flush()

        with _LOCK:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute(
                    """
                    SELECT marker, timestamp
                    FROM archive_markers
                    WHERE session_key = ?
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                    """,
                    (self.session_key, limit, offset),
                )
                rows = cursor.fetchall()

                # 总数（用于分页）
                total = conn.execute(
                    "SELECT COUNT(*) FROM archive_markers WHERE session_key = ?",
                    (self.session_key,),
                ).fetchone()[0]
            finally:
                conn.close()

        import datetime
        result = []
        for marker, ts in rows:
            dt = datetime.datetime.fromtimestamp(ts)
            result.append({
                "marker": marker,
                "timestamp": ts,
                "time_str": dt.strftime("%Y-%m-%d %H:%M"),
            })

        return {
            "total": total,
            "markers": result,
        }

    # ── 维护 ──────────────────────────────────────────

    def close(self):
        self.flush()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


# ── 旧数据迁移 ─────────────────────────────────────

# ── 归档（超限清理时保留被删数据）───────────────

def _save_trash(session_key: str, rows: list[tuple]):
    """将 archive 清理掉的旧记录存入 trash JSONL（纯文本，可 grep）。

    每行一条 JSON：{"ts": ..., "role": ..., "content": ..., "source": ...}
    文件：data/archive_trash/{sanitized_key}.jsonl
    """
    from pathlib import Path as _Path
    trash_dir = _Path(__file__).parent.parent / "data" / "archive_trash"
    trash_dir.mkdir(parents=True, exist_ok=True)

    # session_key 可能有特殊字符，做安全文件名
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", session_key)[:80]
    trash_path = trash_dir / f"{safe}.jsonl"

    try:
        lines = []
        for content, role, ts, source_id in rows:
            rec = {
                "ts": round(ts or time.time(), 2),
                "role": role or "user",
                "content": content[:2000],
                "source": source_id or "",
            }
            lines.append(json.dumps(rec, ensure_ascii=False))
        with open(trash_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        logger.info("📝 归档 %d 条 -> %s", len(rows), trash_path)
    except Exception as e:
        logger.warning("归档Write失败（不影响主流程）: %s", e)


def recent_global(limit: int = 10, hours: int = 72) -> dict:
    """跨 session 获取最近 N 小时的全局 markers（Phase 4 学用对接）。

    不限制 session_key，只按时间过滤。
    用于 session 预热时注入同主题历史。
    """
    import sqlite3, time, datetime

    # 找 archive.db（尝试多个位置）
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "data", "archive.db"),
        os.path.expanduser("~/gbase-home/data/archive.db"),
    ]
    db_path = None
    for c in candidates:
        p = os.path.abspath(c)
        if os.path.exists(p):
            db_path = p
            break
    if not db_path:
        return {"markers": [], "count": 0, "db": None}

    cutoff_ts = time.time() - hours * 3600

    with _LOCK:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT marker, timestamp, session_key FROM archive_markers "
                "WHERE timestamp >= ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (cutoff_ts, limit),
            )
            rows = cursor.fetchall()
            conn.close()
        except Exception:
            return {"markers": [], "count": 0, "db": db_path}

    result = []
    for marker, ts, skey in rows:
        dt = datetime.datetime.fromtimestamp(ts)
        skey_short = skey.split(":")[-1][:20] if skey else ""
        result.append({
            "marker": marker[:120],
            "timestamp": ts,
            "time_str": dt.strftime("%m-%d %H:%M"),
            "session": skey_short,
        })

    return {"markers": result, "count": len(result), "db": db_path}


def _copy_old_data(dat_db_path: str, archive_db_path: str):
    """从 dat.db 导入旧 experience/knowledge 数据到 archive.db（一次性）。"""
    if not os.path.exists(dat_db_path):
        return

    logger.info("正在从 %s 导入旧数据到 %s ...", dat_db_path, archive_db_path)

    tmp_store = ArchiveStore(session_key="_migration_", db_path=archive_db_path)

    try:
        conn = sqlite3.connect(dat_db_path)
        cursor = conn.cursor()

        # 从 entries table 找 experience 和 knowledge
        for tbl, pri in [("entries", 1)]:
            try:
                cursor.execute(f"SELECT content, type FROM {tbl} WHERE content IS NOT NULL AND content != ''")
                for content, typ in cursor.fetchall():
                    p = 0
                    if typ and "know" in typ.lower():
                        p = 1
                    tmp_store.append("assistant", str(content)[:1000], priority=p)
            except sqlite3.OperationalError:
                pass

        tmp_store.flush()
        logger.info("旧数据导入完成")
    except Exception as e:
        logger.warning("旧数据导入失败: %s", e)
    finally:
        conn.close()
