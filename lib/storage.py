# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/storage.py

Storage engine — SQLite primary + JSONL readable mirror dual-write.

栈内存所有经验/知识/Skill 都通过这个模块读写。
"""

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

_MAX_RECORDS = 50
"""每个类型最多保留的记录数（超过删最旧）"""


class Storage:
    """沉淀引擎。

    用法：
        store = Storage()
        store.setup()           # 首次初始化
        store.write("experience", {"summary": "xxx", ...})
        entries = store.read_recent("experience", limit=5)

    线程安全：内部使用 threading.Lock。
    """

    def __init__(self, db_path: str = None, data_dir: str = None):
        self._db_path = db_path or str(DB_PATH)
        self._data_dir = Path(data_dir) if data_dir else DATA_DIR
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

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
            # FTS5 全文索引（支持中文 unicode61 tokenizer）
            # content='entries' 表示不单独存文本，通过 entries 表 rowid 关联
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
                    content, summary,
                    content='entries', content_rowid='id',
                    tokenize='unicode61',
                    detail=column
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

    def write(self, type_: str, entry: dict, summary: str = "",
              confidence: str = "low", **kwargs) -> int:
        """写入一条记录。自动写 SQLite + 追加 JSONL 镜像。

        Args:
            type_: 类型（experience / knowledge / skills）
            entry: 内容字典（会被 JSON 序列化）
            summary: 一句话摘要
            confidence: 确信度（low / medium / high）

        Returns:
            记录的 id（写入成功）或 0（跳过）
        """
        now = time.time()
        content_json = json.dumps(entry, ensure_ascii=False)

        with self._lock:
            if self._conn is None:
                raise RuntimeError("Storage 未初始化，请先调用 setup()")

            # 写入 SQLite
            cursor = self._conn.execute(
                "INSERT INTO entries (type, content, summary, created_at, confidence) "
                "VALUES (?, ?, ?, ?, ?)",
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

            logger.debug("写入 %s[%d]: %s", type_, row_id, summary[:60])
            return row_id

    # ── 读取 ────────────────────────────────────────────

    def read_recent(self, type_: str, limit: int = 5) -> list[dict]:
        """读取最近 N 条记录。

        Args:
            type_: 类型
            limit: 数量

        Returns:
            [{"id": 1, "type": ..., "content": ..., "summary": ...,
              "created_at": ..., "hits": ..., "confidence": ...}, ...]
        """
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
                results.append({
                    "id": row_id,
                    "type": type_,
                    "content": json.loads(content_json),
                    "summary": summary,
                    "created_at": created_at,
                    "hits": hits,
                    "confidence": conf,
                })
            return results

    # ── 命中计数（增加引用权重）──────────────────────────

    def record_hit(self, record_id: int):
        """递增某条记录的 hits 计数。"""
        with self._lock:
            if self._conn is None:
                return
            self._conn.execute(
                "UPDATE entries SET hits = hits + 1 WHERE id = ?",
                (record_id,),
            )
            self._conn.commit()

    # ── 内部方法 ────────────────────────────────────────

    def _prune(self, type_: str):
        """超过上限时，删除最旧的记录。"""
        if self._conn is None:
            return
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM entries WHERE type=?",
            (type_,),
        )
        count = cursor.fetchone()[0]
        if count > _MAX_RECORDS:
            excess = count - _MAX_RECORDS
            self._conn.execute(
                "DELETE FROM entries WHERE id IN ("
                "SELECT id FROM entries WHERE type=? ORDER BY created_at ASC LIMIT ?"
                ")",
                (type_, excess),
            )
            self._conn.commit()
            logger.info("已修剪 %d 条过期 %s 记录", excess, type_)

    # ── 清理 ────────────────────────────────────────────

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
                logger.info("存储引擎已关闭")
