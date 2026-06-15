# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/storage.py

沉淀引擎 — SQLite 主力 + JSONL 可读镜像双写。

栈内存所有经验/知识/Skill 都通过这个模块读写。
"""

import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "dat.db"

# 三层对应的 JSONL 镜像文件名
_MIRROR_FILES = {
    "experience": "experience.jsonl",
    "knowledge": "knowledge.jsonl",
    "skills": "skills.jsonl",
}

# P1: 软上限 — 只删从未被引用（hits=0）且超过 90 天的旧条目
# 原 50 条硬上限是金鱼记忆的根因。现在保护所有被引用过的记录永久保留
_MAX_RECORDS = 50000
_PRUNING_KEEP_DAYS = 90  # hits=0 的记录至少保留 90 天


class Storage:
    """沉淀引擎。

    用法：
        store = Storage()
        store.setup()           # 首次初始化
        store.write("experience", {"summary": "xxx", ...})
        entries = store.read_recent("experience", limit=5)

    线程安全：内部使用 threading.RLock（可重入锁，支持递归调用）。
    """

    def __init__(self, db_path: str = None, data_dir: str = None):
        self._db_path = db_path or str(DB_PATH)
        self._data_dir = Path(data_dir) if data_dir else DATA_DIR
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._setup_ran = False  # 避免 setup() 内的 ALTER 重复执行警告
        self._write_count = 0
        self._last_checkpoint_time = 0.0

    # ── 初始化 ──────────────────────────────────────────

    def setup(self):
        """首次初始化（建表 + 建目录 + WAL 模式）。"""
        os.makedirs(self._data_dir, exist_ok=True)

        with self._lock:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,           -- experience | knowledge | skills
                    content TEXT NOT NULL,        -- JSON 字符串
                    summary TEXT DEFAULT '',       -- 一句话摘要
                    created_at REAL NOT NULL,       -- 时间戳
                    hits INTEGER DEFAULT 0,         -- 被引用次数
                    confidence TEXT DEFAULT 'low'    -- low | medium | high
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_type_created
                ON entries(type, created_at DESC)
            """)
            # 兼容迁移：旧表无 tags/rule/archived/last_accessed_at 列时加上
            with contextlib.suppress(Exception):
                conn.execute("ALTER TABLE entries ADD COLUMN tags TEXT DEFAULT ''")
            with contextlib.suppress(Exception):
                conn.execute("ALTER TABLE entries ADD COLUMN rule TEXT DEFAULT ''")
            with contextlib.suppress(Exception):
                conn.execute("ALTER TABLE entries ADD COLUMN archived INTEGER DEFAULT 0")
            with contextlib.suppress(Exception):
                conn.execute("ALTER TABLE entries ADD COLUMN last_accessed_at REAL DEFAULT 0")
            # FTS5 全文索引（支持中文 unicode61 tokenizer）
            # content='entries' 表示不单独存文本，通过 entries 表 rowid 关联
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
                    content, summary,
                    content='entries', content_rowid='id',
                    tokenize='unicode61',
                    detail=full
                )
            """)
            # 触发器：写入/删除/更新时自动同步 FTS
            conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS entries_fts_ai AFTER INSERT ON entries BEGIN
                    INSERT INTO entries_fts(rowid, content, summary)
                    VALUES (new.id, new.content, new.summary);
                END;
                CREATE TRIGGER IF NOT EXISTS entries_fts_ad AFTER DELETE ON entries BEGIN
                    INSERT INTO entries_fts(entries_fts, rowid, content, summary)
                    VALUES ('delete', old.id, old.content, old.summary);
                END;
                CREATE TRIGGER IF NOT EXISTS entries_fts_au AFTER UPDATE OF content, summary ON entries BEGIN
                    INSERT INTO entries_fts(entries_fts, rowid, content, summary)
                    VALUES ('delete', old.id, old.content, old.summary);
                    INSERT INTO entries_fts(rowid, content, summary)
                    VALUES (new.id, new.content, new.summary);
                END;
            """)
            conn.commit()
            self._conn = conn
            # 重建 FTS（已有的数据未进 FTS）
            try:
                cursor = conn.execute("SELECT COUNT(*) FROM entries_fts")
                fts_count = cursor.fetchone()[0]
                cursor = conn.execute("SELECT COUNT(*) FROM entries")
                total = cursor.fetchone()[0]
                if fts_count < total:
                    conn.executescript("""
                        INSERT INTO entries_fts(entries_fts) VALUES('rebuild');
                    """)
                    logger.info("FTS 索引重建完成: %d 条", total)
            except Exception as rebuild_err:
                logger.warning("FTS 索引重建跳过: %s", rebuild_err)
            logger.info("存储引擎已就绪: %s", self._db_path)

    # ── 写入 ────────────────────────────────────────────

    def _ensure_ready(self):
        """确保 storage 已初始化。必须在 self._lock 内调用。"""
        if self._conn is None:
            self.setup()

    @staticmethod
    def _validate_write(type_: str, summary: str, confidence: str) -> tuple[bool, str]:
        """写入前的轻量验证门。

        Returns:
            (通过?, 拒绝原因)
        """
        # ① 空/过短内容直接跳过
        if not summary or len(summary.strip()) < 10:
            return False, "内容过短或无内容"

        # ② 置信度 low 且内容没有实质信息（低质量噪音）
        low_quality_patterns = ["测试", "test", "常规操作", "正常", "unknown", "默认"]
        if confidence == "low":
            for pat in low_quality_patterns:
                if pat in summary[:20]:
                    return False, f"低置信度且含噪音标记({pat})"

        return True, ""

    def write(self, type_: str, entry: dict, summary: str = "", confidence: str = "low", **kwargs) -> int:
        """写入一条记录。自动写 SQLite + 追加 JSONL 镜像。"""
        _ = kwargs  # noqa: ARG002 — 兼容扩展参数

        # ── 验证门：写入前过滤低质量内容 ──
        _pass, _reason = self._validate_write(type_, summary, confidence)
        if not _pass:
            logger.debug("验证门跳过写入 %s: %s (summary=%s)", type_, _reason, summary[:40])
            return -1

        now = time.time()
        content_json = json.dumps(entry, ensure_ascii=False)

        with self._lock:
            self._ensure_ready()

            # 写入 SQLite
            cursor = self._conn.execute(
                "INSERT INTO entries (type, content, summary, created_at, confidence) VALUES (?, ?, ?, ?, ?)",
                (type_, content_json, summary, now, confidence),
            )
            row_id = cursor.lastrowid
            self._conn.commit()

            # 追加 JSONL 镜像
            mirror_path = self._data_dir / _MIRROR_FILES.get(type_, "unknown.jsonl")
            mirror_entry = {
                "id": row_id,
                "type": type_,
                "entry": entry,
                "summary": summary,
                "created_at": now,
                "confidence": confidence,
            }
            with open(mirror_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(mirror_entry, ensure_ascii=False) + "\n")

            # 检查上限，删除最旧记录
            self._prune(type_)

            self._write_count += 1
            self._maybe_checkpoint()

            logger.debug("写入 %s[%d]: %s", type_, row_id, summary[:60])
            return row_id

    # ── 读取 ────────────────────────────────────────────

    def read_recent(self, type_: str, limit: int = 5) -> list[dict]:
        """读取最近 N 条记录。"""
        with self._lock:
            if self._conn is None:
                return []
            cursor = self._conn.execute(
                "SELECT id, type, content, summary, created_at, hits, confidence "
                "FROM entries WHERE type=? ORDER BY created_at DESC LIMIT ?",
                (type_, limit),
            )
            rows = cursor.fetchall()
            results = []
            for row in rows:
                row_id, type_, content_json, summary, created_at, hits, conf = row
                results.append(
                    {
                        "id": row_id,
                        "type": type_,
                        "content": json.loads(content_json),
                        "summary": summary,
                        "created_at": created_at,
                        "hits": hits,
                        "confidence": conf,
                    }
                )
            return results

    # ── 命中计数（增加引用权重）──────────────────────────

    def record_hit(self, record_id: int):
        """递增某条记录的 hits 计数，并记录最后访问时间。"""
        with self._lock:
            self._ensure_ready()
            self._conn.execute(
                "UPDATE entries SET hits = hits + 1, last_accessed_at = ? WHERE id = ?",
                (time.time(), record_id),
            )
            self._conn.commit()
            self._write_count += 1
            self._maybe_checkpoint()

    def _maybe_checkpoint(self):
        """自动 WAL checkpoint：100次写入或10分钟触发。"""
        now = time.time()
        if self._write_count >= 100 or (self._last_checkpoint_time and now - self._last_checkpoint_time >= 600):
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._write_count = 0
                self._last_checkpoint_time = now
            except Exception:
                pass

    # ── 内部方法 ────────────────────────────────────────

    def _prune(self, type_: str):
        """分级淘汰。必须在 self._lock 内调用。"""
        if self._conn is None:
            return
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM entries WHERE type=?",
            (type_,),
        )
        count = cursor.fetchone()[0]
        if count > _MAX_RECORDS:
            excess = count - _MAX_RECORDS
            cutoff = time.time() - _PRUNING_KEEP_DAYS * 86400
            _deleted = self._conn.execute(
                "DELETE FROM entries WHERE id IN ("
                "SELECT id FROM entries WHERE type=? AND hits=0 AND created_at < ? "
                "ORDER BY created_at ASC LIMIT ?)",
                (type_, cutoff, excess,),
            ).rowcount
            self._conn.commit()
            if _deleted > 0:
                logger.info("已修剪 %d 条从未引用过的 %s 记录（> %d 天）", _deleted, type_, _PRUNING_KEEP_DAYS)

    # ── 清理 ────────────────────────────────────────────

    def apply_aging(self, age_cutoff_days: int = 30, decay: float = 0.5):
        """知识老化：超过 age_cutoff_days 没有访问的记录，hit 值衰减。

        只在每 100 次写入时自动触发。
        Phase 5 增强：hit=1 且 60 天未访问的记录自动清理。
        """
        with self._lock:
            self._ensure_ready()
            cutoff = time.time() - age_cutoff_days * 86400
            # 对 last_accessed_at < cutoff 且 hits > 1 的记录衰减 hits
            cursor = self._conn.execute(
                "UPDATE entries SET hits = MAX(1, CAST(hits * ? AS INTEGER)) "
                "WHERE last_accessed_at > 0 AND last_accessed_at < ? AND hits > 1",
                (decay, cutoff),
            )
            affected = cursor.rowcount
            if affected > 0:
                logger.info("知识老化: %d 条记录 hit 衰减(×%.1f)", affected, decay)

            # ── Phase 5 增强：hit=1 且 60 天未访问 → 自动清理（噪音数据） ──
            _noise_cutoff = time.time() - 60 * 86400
            cursor = self._conn.execute(
                "DELETE FROM entries WHERE hits = 1 AND last_accessed_at < ? "
                "AND last_accessed_at > 0",
                (_noise_cutoff,),
            )
            _noise_count = cursor.rowcount
            if _noise_count > 0:
                logger.info("噪音清理: 删除 %d 条 hit=1 的僵尸记录", _noise_count)

            # ── Phase 5 增强：空 content 记录清理 ──
            cursor = self._conn.execute(
                "DELETE FROM entries WHERE content IS NULL OR TRIM(content) = ''"
            )
            _empty_count = cursor.rowcount
            if _empty_count > 0:
                logger.info("空值清理: 删除 %d 条空 content 记录", _empty_count)

            if affected > 0 or _noise_count > 0 or _empty_count > 0:
                self._conn.commit()

    def _checkpoint(self):
        """主动 checkpoint WAL，防止 WAL 文件膨胀。"""
        try:
            cursor = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            _, pages, _ = cursor.fetchone()
            if pages > 0:
                logger.info("WAL checkpoint: %d pages", pages)
        except Exception as e:
            logger.warning("WAL checkpoint 失败: %s", e)

    def close(self):
        with self._lock:
            if self._conn:
                self._checkpoint()
                self._conn.close()
                self._conn = None
                logger.info("存储引擎已关闭")
