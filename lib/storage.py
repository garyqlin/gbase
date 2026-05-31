# SPDX-License-Identifier: MIT
"""
Gbase storage engine module

Persistence Engine — SQLite primary + JSONL readable mirror dual-write.

All experience/knowledge/skill records are read/written through this module.
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

# JSONL mirror file names for each layer
_MIRROR_FILES = {
    "experience": "experience.jsonl",
    "knowledge": "knowledge.jsonl",
    "skills": "skills.jsonl",
}

# P1: Soft limit — only delete un-referenced (hits=0) records older than 90 days
# Old hard limit of 50 was the root cause of goldfish memory.
# All hit-referenced records are now preserved permanently.
_MAX_RECORDS = 50000
_PRUNING_KEEP_DAYS = 90  # hide unused records after 90 days


class Storage:
    """Storage engine.

    Usage:
        store = Storage()
        store.setup()           # First-time init
        store.write("experience", {"summary": "xxx", ...})
        entries = store.read_recent("experience", limit=5)

    Thread-safe: uses threading.Lock internally.
    """

    def __init__(self, db_path: str = None, data_dir: str = None):
        self._db_path = db_path or str(DB_PATH)
        self._data_dir = Path(data_dir) if data_dir else DATA_DIR
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ── Initialization ────────────────────────────────

    def setup(self):
        """First-time init (create tables + directories + WAL mode)."""
        os.makedirs(self._data_dir, exist_ok=True)

        with self._lock:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,           -- experience | knowledge | skills
                    content TEXT NOT NULL,        -- JSON string
                    summary TEXT DEFAULT '',       -- one-line summary
                    created_at REAL NOT NULL,       -- timestamp
                    hits INTEGER DEFAULT 0,         -- reference count
                    confidence TEXT DEFAULT 'low'    -- low | medium | high
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_type_created
                ON entries(type, created_at DESC)
            """)
            # Compat migration: add tags/rule columns if missing
            with contextlib.suppress(Exception):
                conn.execute("ALTER TABLE entries ADD COLUMN tags TEXT DEFAULT ''")
            with contextlib.suppress(Exception):
                conn.execute("ALTER TABLE entries ADD COLUMN rule TEXT DEFAULT ''")
            # FTS5 full-text index (supports Chinese via unicode61 tokenizer)
            # content='entries' means text is not stored separately, linked via rowid
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
                    content, summary,
                    content='entries', content_rowid='id',
                    tokenize='unicode61',
                    detail=column
                )
            """)
            # Triggers: auto-sync FTS on insert/delete/update
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
            # Rebuild FTS (existing data not yet in FTS)
            try:
                cursor = conn.execute("SELECT COUNT(*) FROM entries_fts")
                fts_count = cursor.fetchone()[0]
                cursor = conn.execute("SELECT COUNT(*) FROM entries")
                total = cursor.fetchone()[0]
                if fts_count < total:
                    conn.executescript("""
                        INSERT INTO entries_fts(entries_fts) VALUES('rebuild');
                    """)
                    logger.info("FTS index rebuild complete: %d entries", total)
            except Exception as rebuild_err:
                logger.warning("FTS index rebuild skipped: %s", rebuild_err)
            logger.info("Storage engine ready: %s", self._db_path)

    # ── Write ──────────────────────────────────────────

    def _ensure_ready(self):
        """Ensure storage is initialized. Must be called inside self._lock."""
        if self._conn is None:
            self.setup()

    def write(self, type_: str, entry: dict, summary: str = "", confidence: str = "low", **kwargs) -> int:
        """Write a record. Auto-writes SQLite + appends JSONL mirror."""
        _ = kwargs  # noqa: ARG002 — compat extension params
        now = time.time()
        content_json = json.dumps(entry, ensure_ascii=False)

        with self._lock:
            self._ensure_ready()

            # Write SQLite
            cursor = self._conn.execute(
                "INSERT INTO entries (type, content, summary, created_at, confidence) VALUES (?, ?, ?, ?, ?)",
                (type_, content_json, summary, now, confidence),
            )
            row_id = cursor.lastrowid
            self._conn.commit()

            # Append to JSONL mirror
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

            # Check limit, delete oldest records
            self._prune(type_)

            logger.debug("Write %s[%d]: %s", type_, row_id, summary[:60])
            return row_id

    # ── Read ──────────────────────────────────────────

    def read_recent(self, type_: str, limit: int = 5) -> list[dict]:
        """Read the most recent N records."""
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

    # ── Hit count (increase reference weight) ─────────

    def record_hit(self, record_id: int):
        """Increment hit count for a record."""
        with self._lock:
            self._ensure_ready()
            self._conn.execute(
                "UPDATE entries SET hits = hits + 1 WHERE id = ?",
                (record_id,),
            )
            self._conn.commit()

    # ── Internal methods ────────────────────────────

    def _prune(self, type_: str):
        """Tiered pruning. Must be called inside self._lock."""
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
                logger.info("Pruned %d un-referenced %s records (> %d days)", _deleted, type_, _PRUNING_KEEP_DAYS)

    # ── Cleanup ───────────────────────────────────────

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
                logger.info("Storage engine closed")
