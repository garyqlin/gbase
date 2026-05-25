# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/mirror.py

Mirror engine — Mirror Layer

不是替代 experience.py，而是在它之上覆盖一个"知道记什么、忘什么、什么时候更新"的层。

核心能力：
1. 记正确做法（不只是教训）
2. 遗忘机制（权重衰减 + 过时淘汰）
3. 正确强化（被验证的正确做法，权重上升）
4. 定时回溯（定期回顾，更新过时的记忆）

哲学根基：
    鉴不在一刻而在每一刻。
    鉴不是记住一切，而是知道记什么、忘什么、什么时候更新。
"""

import json
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
MIRROR_DB = DATA_DIR / "mirror.db"


class MemoryType:
    LESSON = "lesson"
    INSIGHT = "insight"
    PRINCIPLE = "principle"
    PATTERN = "pattern"
    CONTEXT = "context"


_DECAY_RATE = 0.95
_DECAY_INTERVAL = 86400
_MIN_STRENGTH = 0.1
_REVIEW_INTERVAL = 7 * 86400
_MAX_REVIEW_ITEMS = 20

# ── Ebbinghaus (Oblivion 框架) ──
_EBBINGHAUS_T = 50  # 温度：50天半衰期，适合间歇对话场景


def ebbinghaus_retention(n_rounds, utility, frequency, temperature=None):
    """Ebbinghaus 遗忘曲线：R = exp(-n / ((U+F) × T))

    Args:
        n_rounds:  距上次使用的交互轮数（天）
        utility:   效用评分 (0-1)，对应 strength
        frequency: 访问频率 (0-1)，对应 hits/50
        temperature: 温度调节，默认 _EBBINGHAUS_T

    Returns:
        R: 保留评分 (0-1)，1=新鲜，0=完全衰减
    """
    import math

    _t = temperature if temperature is not None else _EBBINGHAUS_T
    _s = (utility + frequency + 0.001) * _t
    return math.exp(-n_rounds / _s) if _s > 0 else 0.0


class Mirror:
    """鉴面引擎。"""

    def __init__(self, db_path: str = None):
        self._db_path = db_path or str(MIRROR_DB)
        self._conn: sqlite3.Connection | None = None

    def setup(self):
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '',
                source TEXT DEFAULT '',
                strength REAL DEFAULT 1.0,
                hits INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                last_access REAL DEFAULT 0,
                last_decay REAL DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mirror_active
            ON memories(is_active, strength DESC)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mirror_type
            ON memories(type, strength DESC)
        """)
        # 注：中文场景不适用FTS5，搜索使用LIKE（数据量可控）

        self._conn.commit()

    def record(self, mtype: str, content: str, tags: list = None, source: str = "", strength: float = 1.0):
        if self._conn is None:
            return
        now = time.time()
        tags_str = ",".join(tags) if tags else ""

        existing = self._find_similar(content, mtype)
        if existing:
            new_strength = min(1.0, existing["strength"] * 1.2)
            # 去重合并：如果新内容更完整，合并内容+标签（方案A+时间戳）
            _merge_content_if_better(self._conn, existing["id"], content, tags_str, now)
            self._conn.execute(
                "UPDATE memories SET strength=?, hits=hits+1, last_access=? WHERE id=?",
                (new_strength, now, existing["id"]),
            )
            self._conn.commit()
            return

        self._conn.execute(
            "INSERT INTO memories (type, content, tags, source, strength, "
            "created_at, last_access, last_decay) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (mtype, content, tags_str, source, strength, now, now, now),
        )
        self._conn.commit()

    def _find_similar(self, content: str, mtype: str) -> dict | None:
        if self._conn is None:
            return None
        keywords = [
            w
            for w in content.replace("，", " ").replace("。", " ").split()
            if len(w) > 1 and w not in ("一个", "这个", "那个", "什么", "怎么", "可以", "就是", "不是")
        ]
        key_set = set(keywords[:5])
        if not key_set:
            return None
        cursor = self._conn.execute(
            "SELECT id, content, strength FROM memories WHERE type=? AND is_active=1 ORDER BY strength DESC LIMIT 10",
            (mtype,),
        )
        for row in cursor.fetchall():
            mem_words = set(row[1].replace("，", " ").replace("。", " ").split())
            if len(key_set & mem_words) >= 2:
                return {"id": row[0], "strength": row[2]}
        return None

    def verify(self, content: str, mtype: str = None):
        if self._conn is None:
            return
        now = time.time()
        cursor = self._conn.execute(
            "SELECT id, strength, verified FROM memories WHERE content=? AND is_active=1"
            + (" AND type=?" if mtype else ""),
            (content, mtype) if mtype else (content,),
        )
        row = cursor.fetchone()
        if row:
            new_strength = min(1.0, row[1] * 1.3)
            self._conn.execute(
                "UPDATE memories SET strength=?, verified=verified+1, hits=hits+1, last_access=? WHERE id=?",
                (new_strength, now, row[0]),
            )
            self._conn.commit()

    def decay(self):
        if self._conn is None:
            return
        now = time.time()
        cutoff = now - _DECAY_INTERVAL
        cursor = self._conn.execute(
            "SELECT id, strength, hits, verified FROM memories WHERE is_active=1 AND last_decay < ?",
            (cutoff,),
        )
        decayed = forgotten = 0
        for row in cursor.fetchall():
            mem_id, strength, hits, verified = row
            protection = min(0.3, (hits * 0.01) + (verified * 0.05))
            new_strength = strength * (_DECAY_RATE + protection)
            if new_strength < _MIN_STRENGTH:
                self._conn.execute("UPDATE memories SET is_active=0, last_decay=? WHERE id=?", (now, mem_id))
                forgotten += 1
            else:
                self._conn.execute(
                    "UPDATE memories SET strength=?, last_decay=? WHERE id=?", (new_strength, now, mem_id)
                )
                decayed += 1
        self._conn.commit()

    def review(self) -> dict:
        if self._conn is None:
            return {"status": "not_initialized"}
        now = time.time()
        report = {"timestamp": now, "checked": 0, "still_valid": 0, "needs_update": 0, "outdated": 0, "items": []}
        cursor = self._conn.execute(
            "SELECT id, type, content, strength, hits, verified, created_at "
            "FROM memories WHERE is_active=1 ORDER BY strength DESC LIMIT ?",
            (_MAX_REVIEW_ITEMS,),
        )
        for row in cursor.fetchall():
            mem_id, mtype, content, strength, hits, verified, created_at = row
            report["checked"] += 1
            age_days = (now - created_at) / 86400
            item = {
                "id": mem_id,
                "type": mtype,
                "content": content[:60],
                "strength": round(strength, 2),
                "age_days": round(age_days, 1),
                "hits": hits,
                "verified": verified,
            }
            if age_days > 30 and verified < 2:
                item["status"] = "可能过时"
                report["outdated"] += 1
            elif age_days > 7 and hits < 2:
                item["status"] = "需要审视"
                report["needs_update"] += 1
            else:
                item["status"] = "有效"
                report["still_valid"] += 1
            report["items"].append(item)
        return report

    def get_injection_text(self, max_items: int = 5, ebbinghaus: bool = True) -> str:
        if self._conn is None:
            return ""
        self.decay()
        cursor = self._conn.execute(
            "SELECT id, type, content, strength, hits, verified, "
            "created_at, last_access "
            "FROM memories WHERE is_active=1 "
            "ORDER BY strength DESC LIMIT ?",  # 先全取，Python 侧排序
            (max_items,),
        )
        rows = cursor.fetchall()
        if ebbinghaus:
            now = time.time()
            scored = []
            for r in rows:
                days = (now - (r[7] if r[7] else r[6])) / 86400
                u = r[3]
                f = min(1.0, r[4] / 50.0)
                r_score = ebbinghaus_retention(max(0, days), u, f)
                scored.append((r_score, r))
            scored.sort(key=lambda x: x[0], reverse=True)
            rows = [s[1] for s in scored[:max_items]]
        if not rows:
            return ""
        icons = {"lesson": "⚠️", "insight": "✅", "principle": "📐", "pattern": "🔄", "context": "📌"}
        lines = []
        for row in rows:
            _, mtype, content, _, _, verified, *_ = row
            icon = icons.get(mtype, "📝")
            vmark = f" [已验证{verified}次]" if verified > 0 else ""
            lines.append(f"- {icon} {content}{vmark}")
        return "\n\n## 🔮 鉴面记忆\n以下是你从过去中学到的、经过筛选的记忆（鉴面引擎自动管理）：\n" + "\n".join(lines)

    def get_stats(self) -> dict:
        if self._conn is None:
            return {"status": "not_initialized"}
        cursor = self._conn.execute("SELECT type, COUNT(*) FROM memories WHERE is_active=1 GROUP BY type")
        type_counts = {r[0]: r[1] for r in cursor.fetchall()}
        cursor = self._conn.execute("SELECT COUNT(*) FROM memories WHERE is_active=0")
        forgotten = cursor.fetchone()[0]
        cursor = self._conn.execute("SELECT COUNT(*) FROM memories WHERE verified > 0")
        verified = cursor.fetchone()[0]
        cursor = self._conn.execute("SELECT AVG(strength) FROM memories WHERE is_active=1")
        avg = cursor.fetchone()[0] or 0.0
        return {
            "total_active": sum(type_counts.values()),
            "total_forgotten": forgotten,
            "total_verified": verified,
            "avg_strength": round(avg, 2),
            "by_type": type_counts,
        }

    def inject_last_context(self, target_bytes: int = 16000) -> str:
        """从最近的 session 文件提取上下文，修复跨对话失忆。

        读取倒数第二个 session JSONL 文件末尾，解析最近几轮对话。

        Returns:
            格式化后的上下文文本，或空字符串（没有 session 文件时）。
        """
        import os
        from pathlib import Path

        # 查找 session 目录
        _db_path_obj = Path(self._db_path) if self._db_path else None
        session_dir = _db_path_obj.parent / "sessions" if _db_path_obj else None
        if not session_dir or not session_dir.exists():
            # 尝试默认路径
            alt = Path("/home/opprime-v2/data/sessions")
            if alt.exists():
                session_dir = alt
            else:
                return ""

        session_files = sorted(Path(session_dir).glob("*.jsonl"), key=os.path.getmtime)

        if len(session_files) < 2:
            return ""

        # 取倒数第二个（最新的是当前对话）
        target_file = session_files[-2]

        try:
            # 直接读文件尾，取最后 N 条 entry
            file_size = os.path.getsize(target_file)
            read_size = min(target_bytes, file_size)
            with open(target_file, encoding="utf-8") as f:
                if read_size < file_size:
                    f.seek(file_size - read_size)
                    # 跳到完整行开头
                    f.readline()
                all_text = f.read()

            # 解析最后几行
            lines = []
            lines.append("--- 上次对话摘要 ---")
            count = 0
            for line in reversed(all_text.split("\n")):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    role = entry.get("role", "?")
                    content = entry.get("content", "")
                    if isinstance(content, str) and len(content) > 5:
                        preview = content[:200].strip()
                        lines.append(f"  [{role}] {preview}")
                        count += 1
                        if count >= 6:
                            break
                except (json.JSONDecodeError, TypeError):
                    continue

            if count > 0:
                lines.append("")
                return "\n".join(lines)
            return ""
        except Exception as e:
            logger.warning("上下文交接失败: %s", e)
            return ""

    def recall(self, query: str, limit: int = 10, ebbinghaus: bool = True) -> list:
        """搜索记忆（LIKE 模糊匹配，中文友好）。

        Oblivion 闭环：检索后回写 hits + last_access，
        使 Ebbinghaus F 分量反映真实访问频率。
        """
        if self._conn is None:
            return []
        if not query or not query.strip():
            return []
        now = time.time()
        like_q = f"%{query}%"
        cursor = self._conn.execute(
            """SELECT id, type, content, strength, hits, verified,
                      created_at, last_access
               FROM memories
               WHERE is_active=1 AND (content LIKE ? OR type LIKE ?)
               ORDER BY strength DESC
               LIMIT ?""",  # Python 侧用 Ebbinghaus 重排
            (like_q, like_q, limit),
        )
        rows = cursor.fetchall()
        if ebbinghaus and rows:
            scored = []
            for r in rows:
                days = (now - (r[7] if r[7] else r[6])) / 86400
                u = r[3]
                f = min(1.0, r[4] / 50.0)
                r_score = ebbinghaus_retention(max(0, days), u, f)
                scored.append((r_score, r))
            scored.sort(key=lambda x: x[0], reverse=True)
            rows = [s[1] for s in scored[:limit]]
        # Oblivion 写回：更新访问统计，闭合 Ebbinghaus 反馈回路
        if rows:
            for r in rows:
                self._conn.execute("UPDATE memories SET hits=hits+1, last_access=? WHERE id=?", (now, r[0]))
            self._conn.commit()
        return [
            dict(zip(["id", "type", "content", "strength", "hits", "verified", "created_at", "last_access"], row, strict=False))
            for row in rows
        ]

    def forget(self, pattern: str) -> int:
        """批量软删除匹配的记忆。返回删除数量。"""
        if self._conn is None:
            return 0
        now = time.time()
        keyword = f"%{pattern}%"
        cursor = self._conn.execute("SELECT id FROM memories WHERE is_active=1 AND content LIKE ?", (keyword,))
        ids = [r[0] for r in cursor.fetchall()]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            self._conn.execute(
                f"UPDATE memories SET is_active=0, last_access=? WHERE id IN ({placeholders})", (now, *ids)
            )
            self._conn.commit()
        return len(ids)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


def _merge_content_if_better(conn, mem_id: int, new_content: str, new_tags: str, now: float):
    """当发现重复记忆时，如果新内容信息更丰富则更新旧记录。
    全自动运行，不会抛出异常影响主流程。"""
    try:
        cursor = conn.execute("SELECT content, tags, strength FROM memories WHERE id=?", (mem_id,))
        row = cursor.fetchone()
        if not row:
            return
        old_content, old_tags, old_strength = row

        if len(new_content) > len(old_content) * 1.3 and len(new_content) > 40:
            merged = old_content + " | " + new_content
            if len(merged) > 800:
                merged = merged[:800] + "..."
            conn.execute("UPDATE memories SET content=?, last_access=? WHERE id=?", (merged, now, mem_id))

        if new_tags:
            old_set = set(t for t in old_tags.split(",") if t)
            for t in new_tags.split(","):
                t = t.strip()
                if t and t not in old_set:
                    old_set.add(t)
            combined = ",".join(sorted(old_set))
            if combined != old_tags:
                conn.execute("UPDATE memories SET tags=? WHERE id=?", (combined, mem_id))
    except Exception:
        logger.warning("合并记忆失败（非阻塞）", exc_info=True)
