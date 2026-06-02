# SPDX-License-Identifier: MIT
"""
gbase/lib/mirror.py

Mirror Engine — Mirror Layer

Not a replacement for experience.py, but a layer on top that knows "what to remember, what to forget, and when to update."

Core capabilities:
1. Record correct practices (not just lessons)
2. Forgetting mechanism (weight decay + obsolescence elimination)
3. Positive reinforcement (verified practices gain weight)
4. Periodic review (regularly review and update outdated memories)

Philosophical foundation:
    Reflection is not in a single moment but in every moment.
    Reflection is not about remembering everything, but knowing what to remember, what to forget, and when to update.
"""

import asyncio
import contextlib
import json
import logging
import math
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

# ── RSI Dual-Knob: Importance-driven Temperature ──
_IMPORTANCE_DEFAULT = 0.5  # default for unlabeled memories
_IMPORTANCE_FROZEN = 0.8  # threshold: memories at or above this are always injected
_IMPORTANCE_FREQ_BUMP = 0.6  # effective importance when frequency > 15%


def _source_to_importance(source: str) -> float:
    """Source tag → importance level.

    Returns importance directly from source string origin.
    Falls back to _IMPORTANCE_DEFAULT for unknown sources.
    """
    mapping = {
        "rule": 1.0,
        "principle": 0.9,
        "experience": 0.6,
        "inspection": 0.6,
    }
    return mapping.get(source, _IMPORTANCE_DEFAULT)


def ebbinghaus_retention(n_rounds, utility, frequency, temperature=None, importance=None):
    """Ebbinghaus forgetting curve: R = exp(-n / ((U+F) × T))

    Args:
        n_rounds:  Rounds since last use (days)
        utility:   Utility score (0-1), corresponds to strength
        frequency: Access frequency (0-1), corresponds to hits/50
        temperature: Temperature tuning, overrides importance-derived T
        importance: Importance (0-1), maps to 20-100 day half-life

    Returns:
        R: Retention score (0-1), 1=fresh, 0=fully decayed
    """
    if temperature is None:
        imp = importance if importance is not None else _IMPORTANCE_DEFAULT
        temperature = 20 + imp * 80
    S = (utility + frequency + 0.001) * temperature
    return math.exp(-n_rounds / S) if S > 0 else 0.0


class Mirror:
    """Mirror engine."""

    def __init__(self, db_path: str = None):
        self._db_path = db_path or str(MIRROR_DB)
        self._conn: sqlite3.Connection | None = None
        # GMem P1: 异步写入队列
        self._async_queue: asyncio.Queue | None = None

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
                is_active INTEGER DEFAULT 1,
                importance REAL DEFAULT 0.5
            )
        """)
        # Migration: add importance column to existing tables (no-op on fresh)
        with contextlib.suppress(sqlite3.OperationalError):
            self._conn.execute("ALTER TABLE memories ADD COLUMN importance REAL DEFAULT 0.5")
        # Triple-Layer Filter: add inject_hits
        with contextlib.suppress(sqlite3.OperationalError):
            self._conn.execute("ALTER TABLE memories ADD COLUMN inject_hits INTEGER DEFAULT 0")
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mirror_active
            ON memories(is_active, strength DESC)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mirror_type
            ON memories(type, strength DESC)
        """)
        # P3: 轻量实体关系图
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS gmem_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_a TEXT NOT NULL,
                entity_b TEXT NOT NULL,
                relation TEXT DEFAULT 'related',
                weight REAL DEFAULT 1.0,
                created_at REAL NOT NULL,
                source TEXT DEFAULT '',
                UNIQUE(entity_a, entity_b, relation)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_entity
            ON gmem_relations(entity_a, entity_b)
        """)

        self._conn.commit()

    def record(
        self,
        mtype: str,
        content: str,
        tags: list = None,
        source: str = "",
        strength: float = 1.0,
        importance: float = None,
    ):
        if self._conn is None:
            return
        now = time.time()
        tags_str = ",".join(tags) if tags else ""
        if importance is None:
            importance = _source_to_importance(source)

        existing = self._find_similar(content, mtype)
        if existing:
            new_strength = min(1.0, existing["strength"] * 1.2)
            # Dedup merge: if new content is richer, merge content+tags (plan A + timestamp)
            _merge_content_if_better(self._conn, existing["id"], content, tags_str, now)
            self._conn.execute(
                "UPDATE memories SET strength=?, hits=hits+1, last_access=?, importance=? WHERE id=?",
                (new_strength, now, importance, existing["id"]),
            )
            self._conn.commit()
            return

        self._conn.execute(
            "INSERT INTO memories (type, content, tags, source, strength, "
            "created_at, last_access, last_decay, importance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mtype, content, tags_str, source, strength, now, now, now, importance),
        )
        self._conn.commit()

        # P3: 自动提取实体关系
        if len(content) > 10:
            try:
                ents = self._extract_entities(content)
                if len(ents) >= 2:
                    for i in range(len(ents)):
                        for j in range(i + 1, len(ents)):
                            self.relate_entities(ents[i], ents[j], "co_occur", source=f"memory:{mtype}")
            except Exception:
                logger.exception("静默异常")

    # ── GMem P0: 搜索结果自动沉淀 ──
    SEARCH_TTL = {"fresh": 3600, "normal": 21600, "stale": 86400}
    """搜索结果的 TTL（秒）：fresh < 1h, normal < 6h, stale < 24h。"""

    def record_search(self, query: str, summary: str, depth: int = 0):
        """将搜索结果写入 mirror.

        Args:
            query: 搜索关键词
            summary: 结果摘要（前 500 字 + 链接列表）
            depth: 搜索深度（第几次搜索），影响 importance
        """
        if self._conn is None:
            return
        now = time.time()
        importance = min(1.0, 0.2 + depth * 0.15)  # 首次=0.35, 深度5+=0.95
        # 从 query 提取关键词作为 tags
        tokens = query.replace("，", " ").replace("。", " ").split()
        tags_list = ["search"] + [t for t in tokens if len(t) >= 2][:5]
        tags_str = ",".join(tags_list)
        content = (summary or "")[:1000]
        if not content:
            return

        existing = self._find_similar(query[:80], "search_result")
        if existing:
            # 已有同查询结果 → 仅更新 hits 和概要
            _merge_content_if_better(self._conn, existing["id"], content, tags_str, now)
            self._conn.execute(
                "UPDATE memories SET strength=?, hits=hits+1, last_access=?, importance=? WHERE id=?",
                (min(1.0, existing["strength"] * 1.1), now, importance, existing["id"]),
            )
            self._conn.commit()
            return

        self._conn.execute(
            "INSERT INTO memories (type, content, tags, source, strength, "
            "created_at, last_access, last_decay, importance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("search_result", content, tags_str, f"search:{query}", 0.8, now, now, now, importance),
        )
        self._conn.commit()
        logger.info("GMem: 搜索结果已沉淀 (%s, depth=%d)", query[:40], depth)

    def _prune_search_results(self, max_age: float = 86400):
        """清理超过 TTL 的搜索结果。"""
        if self._conn is None:
            return 0
        cutoff = time.time() - max_age
        cursor = self._conn.execute(
            "DELETE FROM memories WHERE type='search_result' AND created_at < ? AND importance < 0.5",
            (cutoff,),
        )
        deleted = cursor.rowcount
        self._conn.commit()
        if deleted:
            logger.info("GMem: 清理过期搜索结果 %d 条", deleted)
        return deleted

    # ── GMem P1: 异步写入队列 ──
    async def async_record(self, *args, **kwargs):
        """将 record 提交到异步队列，不阻塞主线程。"""
        if self._async_queue is None:
            # 降级为同步
            self.record(*args, **kwargs)
            return
        await self._async_queue.put((args, kwargs))

    async def _drain_async_queue(self):
        """后台协程：消费异步队列中的 record 任务。"""
        while True:
            try:
                args, kwargs = await self._async_queue.get()
                try:
                    self.record(*args, **kwargs)
                except Exception as e:
                    logger.warning("async mirror.record 失败: %s", e)
                finally:
                    self._async_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1)

    def start_async_worker(self):
        """启动异步后台工作者（返回 task 供主协程管理）。"""
        if self._async_queue is None:
            self._async_queue = asyncio.Queue(maxsize=200)
        return asyncio.create_task(self._drain_async_queue())

    # ── GMem P3: 实体关系图 ──
    @staticmethod
    def _extract_entities(text: str) -> list[str]:
        """从文本中提取可能为实体的名词性短语（2-6字中英文词）。
        对中文无空格文本做 bigram（双字滑动窗口）分词增强。
        """
        # 标点替换为空格
        for ch in "，。！？：；、（）\"\"''【】「」『』《》<>…—～·":
            text = text.replace(ch, " ")

        tokens = []
        for w in text.split():
            w = w.strip("""\'()[]【】「」『』《》<>【】『』""").strip()  # noqa: B005
            if not w or w.isdigit():
                continue
            if 2 <= len(w) <= 30:
                tokens.append(w)
            elif len(w) == 1 and w.isalpha():
                pass

        # 对长中文连续文本补充 bigram（双字滑动窗口）分词
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        total_letters = sum(1 for c in text if c.isalpha())
        if total_letters > 0 and chinese_chars / total_letters > 0.5:
            for i in range(len(text) - 1):
                pair = text[i : i + 2]
                if (
                    "\u4e00" <= pair[0] <= "\u9fff"
                    and "\u4e00" <= pair[1] <= "\u9fff"
                    and pair
                    not in (
                        "一个",
                        "这个",
                        "那个",
                        "什么",
                        "怎么",
                        "可以",
                        "就是",
                        "不是",
                        "没有",
                        "我们",
                        "你们",
                        "他们",
                        "已经",
                        "知道",
                        "看到",
                        "需要",
                        "是否",
                        "因为",
                        "所以",
                        "但是",
                        "如果",
                        "虽然",
                        "而且",
                        "或者",
                        "关于",
                        "目前",
                        "现在",
                        "之前",
                        "之后",
                        "以上",
                        "以下",
                        "还是",
                        "一种",
                        "那么",
                        "这样",
                        "那里",
                        "这里",
                    )
                ):
                    tokens.append(pair)

        # 去重 + 截断
        seen = set()
        unique = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique[:8]

    def relate_entities(
        self, entity_a: str, entity_b: str, relation: str = "co_occur", weight: float = 1.0, source: str = ""
    ):
        """记录两个实体之间的关系。"""
        if self._conn is None:
            return
        if entity_a == entity_b:
            return
        # 保留原始调用顺序，不 sorted——查询时双向匹配
        a, b = entity_a.lower().strip(), entity_b.lower().strip()
        # 统一按字母序存储以确保唯一性（INSERT OR IGNORE 依赖 UNIQUE 约束）
        store_a, store_b = (a, b) if a < b else (b, a)
        now = time.time()
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO gmem_relations (entity_a, entity_b, relation, weight, created_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (store_a, store_b, relation, weight, now, source),
            )
            # 权重累加（已有则加权重）
            self._conn.execute(
                "UPDATE gmem_relations SET weight = weight + ? WHERE entity_a=? AND entity_b=? AND relation=?",
                (weight * 0.1, store_a, store_b, relation),
            )
            self._conn.commit()
        except Exception:
            logger.exception("静默异常")

    def query_relations(self, entity: str, max_depth: int = 2) -> list[dict]:
        """查询一个实体的关联网络（多跳）。"""
        if self._conn is None:
            return []
        entity = entity.lower().strip()
        try:
            cursor = self._conn.execute(
                """
                WITH RECURSIVE related(id, ent_a, ent_b, rel, depth) AS (
                    SELECT id, entity_a, entity_b, relation, 1
                    FROM gmem_relations
                    WHERE entity_a = ? OR entity_b = ?
                    UNION
                    SELECT r.id, r.entity_a, r.entity_b, r.relation, rd.depth + 1
                    FROM gmem_relations r
                    JOIN related rd ON (rd.ent_a = r.entity_a OR rd.ent_a = r.entity_b
                                     OR rd.ent_b = r.entity_a OR rd.ent_b = r.entity_b)
                    WHERE rd.depth < ?
                )
                SELECT DISTINCT ent_a, ent_b, rel, depth FROM related
                ORDER BY depth, ent_a
                LIMIT 30
                """,
                (entity, entity, max_depth),
            )
            results = []
            seen_pairs = set()
            for row in cursor.fetchall():
                pair = (row[0], row[1])
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                results.append(
                    {
                        "entity_a": row[0],
                        "entity_b": row[1],
                        "relation": row[2],
                        "depth": row[3],
                    }
                )
            return results
        except Exception:
            return []

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
            "SELECT id, strength, hits, verified, importance FROM memories WHERE is_active=1 AND last_decay < ?",
            (cutoff,),
        )
        decayed = forgotten = 0
        for row in cursor.fetchall():
            mem_id, strength, hits, verified, importance = row
            protection = min(0.3, (hits * 0.01) + (verified * 0.05))
            # Importance protection: high-importance memories decay slower
            if importance and importance >= _IMPORTANCE_FROZEN:
                protection += 0.1
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
                item["status"] = "possibly outdated"
                report["outdated"] += 1
            elif age_days > 7 and hits < 2:
                item["status"] = "needs review"
                report["needs_update"] += 1
            else:
                item["status"] = "valid"
                report["still_valid"] += 1
            report["items"].append(item)
        return report

    # ── Intent dimension labels (for triple-layer filter) ──
    _INTENT_DIMENSIONS = {  # tag prefix -> dimension
        "install": "operation",
        "config": "operation",
        "debug": "operation",
        "deploy": "operation",
        "fix": "operation",
        "troubleshoot": "operation",
        "run": "operation",
        "setup": "operation",
        "architecture": "architecture",
        "pattern": "architecture",
        "principle": "architecture",
        "design": "architecture",
        "structure": "architecture",
        "fact": "fact",
        "context": "fact",
        "person": "fact",
        "history": "fact",
        "lesson": "experience",
        "anti.pattern": "experience",
        "failure": "experience",
        "warning": "experience",
        "gotcha": "experience",
        "research": "research",
        "paper": "research",
        "survey": "research",
        "arxiv": "research",
        "reference": "research",
    }

    def _score_intent(self, content: str, tags: str, user_input: str = "") -> float:
        """Layer 1: Intent match score (rule-based, LLM-free fallback).

        Compares the dominant dimension of the memory's tags
        against the dominant dimension of the user's input.
        1.0 = exact match, 0.3 = vague match, 0.0 = mismatch / no intent.
        """
        if not user_input:
            return 0.3  # no user context -> slight penalty, don't overfilter

        def _detect_dimensions(text: str) -> dict:
            scores = {"operation": 0, "architecture": 0, "fact": 0, "experience": 0, "research": 0}
            text_lower = text.lower()
            for keyword, dim in self._INTENT_DIMENSIONS.items():
                if keyword in text_lower:
                    scores[dim] = scores.get(dim, 0) + 1
            # Heuristic nudges based on content patterns
            if any(q in text_lower for q in ["how to", "how do", "steps", "install", "run", "debug", "fix"]):
                scores["operation"] += 1
            if any(q in text_lower for q in ["why", "architecture", "design", "principle", "pattern", "structure"]):
                scores["architecture"] += 1
            if any(q in text_lower for q in ["who is", "what is", "when did", "where", "paper", "research"]):
                scores["research"] += 1
            if any(q in text_lower for q in ["lesson", "mistake", "error", "fail", "gotcha", "warning"]):
                scores["experience"] += 1
            return scores

        mem_dims = _detect_dimensions(content + " " + tags)
        user_dims = _detect_dimensions(user_input)

        # Find dominant dimension for each
        mem_dominant = max(mem_dims, key=mem_dims.get) if max(mem_dims.values()) > 0 else None
        user_dominant = max(user_dims, key=user_dims.get) if max(user_dims.values()) > 0 else None

        if mem_dominant is None or user_dominant is None:
            return 0.3  # no clear intent in either side

        if mem_dominant == user_dominant:
            return 1.0

        # Partial matches: operation <-> experience, architecture <-> research
        partial_pairs = {
            ("operation", "experience"),
            ("experience", "operation"),
            ("architecture", "research"),
            ("research", "architecture"),
            ("fact", "research"),
            ("research", "fact"),
        }
        if (mem_dominant, user_dominant) in partial_pairs:
            return 0.6

        return 0.0

    def _score_feedback(self, inject_hits: int, days_since_created: float) -> float:
        """Layer 2: Trigger rate feedback.

        Memories that have been injected many times relative to their age
        get a high score. Zombie memories (old, zero hits) get nearly zero.
        """
        if days_since_created < 1:
            return 0.8  # too young to judge
        if inject_hits == 0 and days_since_created > 90:
            return 0.05  # zombie
        if inject_hits == 0 and days_since_created > 30:
            return 0.3  # low-potential

        expected = max(1, days_since_created / 30)
        ratio = inject_hits / expected
        if ratio >= 1.0:
            return 1.0
        if ratio >= 0.3:
            return 0.8
        if ratio >= 0.1:
            return 0.3
        return 0.1

    @staticmethod
    def _score_density(content: str) -> float:
        """Layer 3: Information density.

        High-density: short, actionable, contains commands/numbers/code patterns.
        Low-density: long prose summaries with no actionable conclusions.
        """
        length = len(content)
        if length < 30:
            return 1.0  # very short -> dense by nature

        # Actionable signals
        actionable = 0
        if any(
            kw in content
            for kw in [
                "→",
                "`",
                "pip ",
                "import ",
                "sudo",
                "curl",
                "POST",
                "Error:",
                "failed",
                "fix:",
                "rule:",
                "Don't",
                "Never",
            ]
        ):
            actionable += 2
        if any(kw in content for kw in ["步骤", "第一步", "命令", "配置", "参数"]):
            actionable += 2

        # Punctuation density (more punctuation per char = more structured = higher density)
        punct_count = sum(1 for c in content if c in "!?.,:;")
        density = min(1.0, (actionable + punct_count / max(1, length) * 50) / 4.0)

        # Long prose penalty
        if length > 200:
            density *= 0.6
        if length > 500:
            density *= 0.4

        return max(0.05, density)

    # ── Noise tag patterns (always penalized) ──
    _NOISE_TAGS = {"short_reply", "api_error", "generic", "auto.success", "auto.success:after"}
    _NOISE_EXPERIENCE_PATTERNS = [
        "回复长度偏短",
        "下次应尽量提供",
        "AI助手应明确说明",
        "运行成功",
        "成功完成",
    ]

    @staticmethod
    def _is_noise_memory(tags: str, content: str) -> bool:
        """Detect low-value noise memories that should be aggressively down-weighted."""
        for pattern in Mirror._NOISE_EXPERIENCE_PATTERNS:
            if pattern in content:
                return True
        return bool("auto.success" in tags or "short_reply" in tags or "api_error" in tags)

    def _rate_candidates(self, rows: list, user_input: str, max_items: int = 5, ebbinghaus: bool = True) -> list:
        """Triple-layer scoring on candidate memories.

        Frozen rules (importance >= 0.8) bypass scoring entirely.
        Returns scored and capped list.
        """
        frozen = [r for r in rows if r[8] >= _IMPORTANCE_FROZEN]
        dynamic = [r for r in rows if r[8] < _IMPORTANCE_FROZEN]

        now = time.time()
        scored = []
        for r in dynamic:
            content = r[2]
            tags = self._get_tags(r[0])  # fetch tags for this memory
            days = (now - (r[7] if r[7] else r[6])) / 86400

            # Pre-filter: aggressively down-weight noise memories
            is_noise = self._is_noise_memory(tags, content)
            noise_penalty = 0.1 if is_noise else 1.0

            # Layer 1: Intent
            s1 = self._score_intent(content, tags, user_input)
            if s1 == 0.0:
                continue  # hard filter: wrong intent, skip entirely

            # Layer 2: Trigger rate feedback
            inject_hits = self._get_inject_hits(r[0])
            s2 = self._score_feedback(inject_hits, days)

            # Layer 3: Density
            s3 = self._score_density(content)

            # Ebbinghaus curve as base
            if ebbinghaus:
                U = r[3]
                F = min(1.0, r[4] / 50.0)
                imp = r[8]
                effective_imp = imp
                if F > 0.15 and imp < _IMPORTANCE_FREQ_BUMP:
                    effective_imp = _IMPORTANCE_FREQ_BUMP
                R = ebbinghaus_retention(max(0, days), U, F, importance=effective_imp)
            else:
                R = 1.0

            # Weighted fusion with noise penalty
            W = 0.5 * s1 + 0.25 * s2 + 0.25 * s3
            final_score = R * W * noise_penalty
            scored.append((final_score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        selected = frozen + [s[1] for s in scored[:max_items]]

        # Update inject_hits for selected memories
        for r in selected:
            self._increment_inject_hits(r[0])

        return selected[:max_items]

    def _get_tags(self, memory_id: int) -> str:
        """Fetch tags for a memory by id."""
        if self._conn is None:
            return ""
        cursor = self._conn.execute("SELECT tags FROM memories WHERE id=?", (memory_id,))
        row = cursor.fetchone()
        return row[0] if row else ""

    def _get_inject_hits(self, memory_id: int) -> int:
        """Fetch inject_hits counter for a memory."""
        if self._conn is None:
            return 0
        cursor = self._conn.execute("SELECT inject_hits FROM memories WHERE id=?", (memory_id,))
        row = cursor.fetchone()
        return row[0] if row else 0

    def _increment_inject_hits(self, memory_id: int):
        """Increment inject_hits for a memory."""
        if self._conn is None:
            return
        self._conn.execute("UPDATE memories SET inject_hits = inject_hits + 1 WHERE id=?", (memory_id,))
        self._conn.commit()

    def get_injection_text(
        self, max_items: int = 5, ebbinghaus: bool = True, user_input: str = "", tier: str = "auto"
    ) -> str:
        """Return candidate memories with triple-layer scoring.

        Args:
            max_items: Max memories to inject (default 5).
            ebbinghaus: Apply Ebbinghaus curve (default True).
            user_input: Current user message for intent matching.
            tier: Injection tier.
                "hot" — only high inject_hits (>=5) + lesson type.
                "warm" — keyword-matched pool via recall().
                "cold" — full recall, use search_self tool manually.
                "auto" (default) — hot first (3), then warm (remaining).

        Returns:
            Formatted markdown section with scored memories, or empty string.
        """
        if self._conn is None:
            return ""
        self.decay()

        if tier == "hot":
            cursor = self._conn.execute(
                "SELECT id, type, content, strength, hits, verified, "
                "created_at, last_access, importance, inject_hits "
                "FROM memories WHERE is_active=1 AND type IN ('lesson') "
                "AND inject_hits >= 5 "
                "ORDER BY inject_hits DESC, strength DESC LIMIT ?",
                (max(max_items, 3),),
            )
            rows = cursor.fetchall()
            if not rows:
                return ""
            result = self._rate_candidates(rows, user_input, max_items=3, ebbinghaus=ebbinghaus)
            label = "\U0001f525 Hot Memory"

        elif tier == "warm":
            limit = max(max_items, 8)
            if user_input:
                warm_results = self.recall(user_input, limit=limit, ebbinghaus=ebbinghaus)
                if warm_results:
                    result = warm_results[:max_items]
                else:
                    cursor = self._conn.execute(
                        "SELECT id, type, content, strength, hits, verified, "
                        "created_at, last_access, importance, inject_hits "
                        "FROM memories WHERE is_active=1 AND importance >= 0.8 "
                        "ORDER BY inject_hits DESC LIMIT ?",
                        (limit,),
                    )
                    rows = cursor.fetchall()
                    result = self._rate_candidates(rows, user_input, max_items=5, ebbinghaus=ebbinghaus)
            else:
                result = []
            label = "\U0001f3a5 Warm Memory"

        else:
            cursor = self._conn.execute(
                "SELECT id, type, content, strength, hits, verified, "
                "created_at, last_access, importance, inject_hits "
                "FROM memories WHERE is_active=1 "
                "ORDER BY strength DESC LIMIT ?",
                (max_items * 3,),
            )
            rows = cursor.fetchall()
            if not rows:
                return ""
            result = self._rate_candidates(rows, user_input, max_items=max_items, ebbinghaus=ebbinghaus)
            label = "\U0001f52e Mirror Memory"

        if not result:
            return ""
        icons = {
            "lesson": "\u26a0\ufe0f",
            "insight": "\u2705",
            "principle": "\U0001f4d0",
            "pattern": "\U0001f504",
            "context": "\U0001f4cc",
        }
        lines = []
        for row in result:
            if isinstance(row, dict):
                mtype = row.get("type", "")
                content = row.get("content", "")
                verified = row.get("verified", 0)
            else:
                _, mtype, content, _, _, verified, *_ = row
            icon = icons.get(mtype, "\U0001f4dd")
            vmark = f" [verified {verified}x]" if verified > 0 else ""
            lines.append(f"- {icon} {content}{vmark}")
        return f"\n\n## {label}\nRelevant memories from past conversations:\n" + "\n".join(lines)

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
        """Extract context from recent session files to fix cross-conversation amnesia.

        Read the tail of the second-to-last session JSONL file, parse recent conversation rounds.

        Returns:
            Formatted context text, or empty string (when no session files exist).
        """
        import os
        from pathlib import Path

        # Find session directory
        _db_path_obj = Path(self._db_path) if self._db_path else None
        session_dir = _db_path_obj.parent / "sessions" if _db_path_obj else None
        if not session_dir or not session_dir.exists():
            # Try default path
            alt = Path("/home/gbase-v2/data/sessions")
            if alt.exists():
                session_dir = alt
            else:
                return ""

        session_files = sorted(Path(session_dir).glob("*.jsonl"), key=os.path.getmtime)

        if len(session_files) < 2:
            return ""

        # Take the second-to-last (the latest is current conversation)
        target_file = session_files[-2]

        try:
            # Read file tail directly, take last N entries
            file_size = os.path.getsize(target_file)
            read_size = min(target_bytes, file_size)
            with open(target_file, encoding="utf-8") as f:
                if read_size < file_size:
                    f.seek(file_size - read_size)
                    # Skip to start of complete line
                    f.readline()
                all_text = f.read()

            # Parse last few lines
            lines = []
            lines.append("--- Last conversation summary ---")
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
            logger.warning("Context handoff failed: %s", e)
            return ""

    @staticmethod
    def _expand_recall_query(query: str) -> list:
        """Expand query into multiple LIKE-friendly sub-queries.

        Uses jieba for fine-grained Chinese word segmentation so that
        long Chinese sentences get broken into short tokens/ngrams
        for OR-style LIKE matching.
        """
        import re

        import jieba

        # Clean punctuation / whitespace
        clean = re.sub(r"[，。！？、；：" "''\\s,.!?;:()（）\\[\\]【】\\{\\}<>《》/\\|@#\\$%^&*+=\\-~]+", " ", query)
        clean = clean.strip()
        if not clean:
            return [query] if query else []

        # jieba segmentation
        tokens = [w.strip() for w in jieba.cut(clean) if w.strip() and len(w.strip()) >= 2]

        # Sliding 2-char n-grams for long tokens (>4 chars)
        ngrams = []
        for t in tokens:
            if len(t) <= 4:
                ngrams.append(t)
            else:
                for i in range(len(t) - 1):
                    chunk = t[i : i + 2]
                    if len(chunk) == 2:
                        ngrams.append(chunk)

        # Dedup + limit
        seen = set()
        unique = []
        for term in tokens + ngrams:
            term = term.strip()
            if term and term not in seen:
                seen.add(term)
                unique.append(term)

        # Full query as precision anchor
        result = [clean] if clean else []
        result.extend(unique[:8])
        return result

    def recall(self, query: str, limit: int = 10, ebbinghaus: bool = True, include_forgotten: bool = False, open_recall: bool = False, relevance: float = 0.0) -> list:
        """Search memories with multi-phrase LIKE expansion.

        Instead of a single LIKE '%whole sentence%', expands the query
        into multiple sub-queries (full sentence + extracted key phrases)
        combined with OR, so Chinese long sentences can find partial matches.

        Oblivion loop: write back hits + last_access after retrieval,
        closes the Ebbinghaus feedback loop.
        """
        if self._conn is None:
            return []
        if not query or not query.strip():
            return []
        now = time.time()

        # ── Expand query into multiple search terms ──
        terms = self._expand_recall_query(query)
        active_clause = "" if (include_forgotten or open_recall) else "is_active=1 AND"

        if not terms:
            like_q = f"%{query}%"
            cursor = self._conn.execute(
                f"""SELECT id, type, content, strength, hits, verified,
                          created_at, last_access, is_active
                   FROM memories
                   WHERE {active_clause} (content LIKE ? OR type LIKE ?)
                   ORDER BY strength DESC
                   LIMIT ?""",
                (like_q, like_q, limit),
            )
            rows = cursor.fetchall()
        else:
            # Build WHERE: (full_match) OR (term1 LIKE) OR (term2 LIKE) OR ...
            clauses = []
            params = []
            for term in terms:
                t = f"%{term}%"
                clauses.append("(content LIKE ? OR type LIKE ?)")
                params.extend([t, t])

            sql = f"""SELECT id, type, content, strength, hits, verified,
                          created_at, last_access, is_active
                   FROM memories
                   WHERE {active_clause} ({" OR ".join(clauses)})
                   ORDER BY strength DESC
                   LIMIT ?"""
            cursor = self._conn.execute(sql, (*params, limit * 3))  # fetch more for dedup
            rows = cursor.fetchall()

            # Deduplicate by id, keep first occurrence (highest strength)
            seen_ids = set()
            deduped = []
            for r in rows:
                if r[0] not in seen_ids:
                    seen_ids.add(r[0])
                    deduped.append(r)
            rows = deduped[: limit * 2]  # still keep extra for Ebbinghaus re-rank
        if ebbinghaus and rows:
            scored = []
            for r in rows:
                days = (now - (r[7] if r[7] else r[6])) / 86400
                U = r[3]
                F = min(1.0, r[4] / 50.0)
                R = ebbinghaus_retention(max(0, days), U, F)
                scored.append((R, r))
            scored.sort(key=lambda x: x[0], reverse=True)
            rows = [s[1] for s in scored[:limit]]
        # Oblivion write-back: update access stats, close Ebbinghaus feedback loop
        if rows:
            boost = 0.05 + relevance * 0.10  # contextual blood return: relevance 0→1 maps to +0.05→+0.15
            for r in rows:
                was_inactive = (len(r) > 8 and not r[8])  # is_active=0 means archived
                if was_inactive and open_recall:
                    # revive: bring archived memory back to active pool
                    self._conn.execute(
                        "UPDATE memories SET strength=MIN(strength + ?, 2.0), hits=hits+1, is_active=1, last_access=? WHERE id=?",
                        (boost, now, r[0]))
                else:
                    self._conn.execute(
                        "UPDATE memories SET hits=hits+1, last_access=? WHERE id=?",
                        (now, r[0]))
            self._conn.commit()
        return [
            dict(zip(["id", "type", "content", "strength", "hits", "verified", "created_at", "last_access", "is_active"], row))
            for row in rows
        ]

    def forget(self, pattern: str) -> int:
        """Batch soft-delete matching memories. Returns deletion count."""
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

    # ── 冷记忆搜索（已遗忘的记忆，主动查仍可找到）──

    def search_cold(self, query: str, limit: int = 10) -> list[dict]:
        """Search only cold (is_active=0) memories.

        Even after automatic forgetting, memories are preserved with
        is_active=0 so they can still be found on explicit search.
        """
        return self.recall(query, limit=limit, include_forgotten=True)

    # ── GMem: P0 记忆预调度 ──

    def predict(self, query: str, top_k: int = 5) -> list[dict]:
        """根据用户消息预加载相关记忆。

        在 system prompt 构建时调用，不等 tool call。
        1. 精确匹配（recall 原有逻辑）
        2. 主题关键词发散搜索
        3. P3: 实体关系图扩展
        4. 去重合并 + Ebbinghaus 排序
        """
        if self._conn is None or not query or not query.strip():
            return []

        # 0. 搜索类型记忆特殊处理：降权旧结果
        now = time.time()

        # 1. 精确匹配（排除 type=search_result 的旧数据）
        exact = []
        for r in self.recall(query, limit=5):
            if r.get("type") == "search_result":
                age_hours = (now - r.get("last_access", now)) / 3600
                age_day = (now - r.get("created_at", now)) / 86400
                if age_hours < 1:
                    r["_priority"] = 10  # < 1h → 高优先
                elif age_hours < 6:
                    r["_priority"] = 3
                elif age_day < 1:
                    r["_priority"] = 1
                else:
                    continue  # > 24h → 不命中
                exact.append(r)
            else:
                exact.append(r)
        exact = exact[:3]

        # 2. 主题扩展：从 query 提取核心双字词和名词
        tokens = query.replace("，", " ").replace("。", " ").replace("？", " ").replace("！", " ").split()
        # 过滤停用词，提取 >= 2 字词
        stop_words = {
            "一个",
            "这个",
            "那个",
            "什么",
            "怎么",
            "可以",
            "就是",
            "不是",
            "还是",
            "没有",
            "我们",
            "你们",
            "他们",
            "已经",
            "知道",
            "看到",
            "需要",
            "是否",
            "因为",
            "所以",
            "但是",
            "如果",
        }
        keywords = [w for w in tokens if len(w) >= 2 and w not in stop_words][:5]

        # 用关键词分别搜索
        expanded = []
        seen_ids = {r["id"] for r in exact}
        for kw in keywords:
            for r in self.recall(kw, limit=3, ebbinghaus=True):
                if r["id"] not in seen_ids:
                    expanded.append(r)
                    seen_ids.add(r["id"])

        # 3. P3: 实体关系图扩展
        entities = self._extract_entities(query)
        if len(entities) >= 1:
            # 通过实体查关系网络
            for ent in entities[:3]:
                relations = self.query_relations(ent, max_depth=2)
                for rel in relations:
                    # 关联实体回流搜索
                    for candidate in (rel["entity_a"], rel["entity_b"]):
                        if candidate == ent.lower().strip():
                            continue
                        for r in self.recall(candidate, limit=2, ebbinghaus=True):
                            if r["id"] not in seen_ids:
                                expanded.append(r)
                                seen_ids.add(r["id"])

        # 4. 合并去重 + Ebbinghaus 排序（先精确后扩展）
        combined = exact + expanded[: max(0, top_k - len(exact))]

        # 按 strength 降序
        combined.sort(key=lambda x: x.get("strength", 0), reverse=True)

        return combined[:top_k]

    # ── GMem: P2 经验标准化（export/import）──

    GMEM_VERSION = "1.0"

    def export(self, selector: str = "", limit: int = 1000) -> str:
        """记忆 → GMem 标准格式 JSONL。按 selector 筛选 content 模糊匹配。"""
        if self._conn is None:
            return ""
        try:
            if selector:
                cursor = self._conn.execute(
                    "SELECT type, content, source, hits, strength, importance, tags, created_at FROM memories "
                    "WHERE is_active=1 AND content LIKE ? ORDER BY strength DESC LIMIT ?",
                    (f"%{selector}%", limit),
                )
            else:
                cursor = self._conn.execute(
                    "SELECT type, content, source, hits, strength, importance, tags, created_at FROM memories "
                    "WHERE is_active=1 ORDER BY strength DESC LIMIT ?",
                    (limit,),
                )
            lines = []
            for row in cursor.fetchall():
                mtype, content, source, hits, strength, importance, tags_str, created_at = row
                entry = {
                    "version": self.GMEM_VERSION,
                    "type": mtype,
                    "content": content,
                    "source": source or "unknown",
                    "tags": tags_str.split(",") if tags_str else [],
                    "confidence": "high" if strength >= 0.8 else ("medium" if strength >= 0.5 else "low"),
                    "importance": importance or 0.5,
                    "hits": hits,
                    "created_at": created_at,
                }
                lines.append(json.dumps(entry, ensure_ascii=False))
            return "\n".join(lines)
        except Exception as e:
            logger.warning("mirror.export 失败: %s", e)
            return ""

    def import_from(self, jsonl_text: str, source_tag: str = "gmem-import", strict: bool = False) -> int:
        """导入 GMem 标准 JSONL 格式的记忆，自动去重。

        Args:
            jsonl_text: GMem 标准 JSONL 字符串
            source_tag: 批量导入的 source 标记
            strict: 严格模式——校验 version 字段必须匹配
        Returns:
            实际导入条数
        """
        if self._conn is None:
            return 0
        count = 0
        for line in jsonl_text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                content = entry.get("content", "")
                if not content:
                    continue
                # P2: 版本校验（严格模式）
                if strict and entry.get("version") != self.GMEM_VERSION:
                    logger.warning(
                        "GMem import: 版本不匹配 (got %s, want %s), 跳过", entry.get("version"), self.GMEM_VERSION
                    )
                    continue
                mtype = entry.get("type", "lesson")
                # 去重：检查内容相似度
                existing = self._find_similar(content, mtype)
                if existing:
                    continue
                tags = entry.get("tags", [])
                if isinstance(tags, list):
                    tags.append(source_tag)
                imp = entry.get("importance", 0.5)
                strength_map = {"high": 0.9, "medium": 0.6, "low": 0.3}
                strength = strength_map.get(entry.get("confidence", "medium"), 0.6)
                self.record(
                    mtype=mtype,
                    content=content,
                    tags=tags,
                    source=entry.get("source", "gmem-import"),
                    strength=strength * (1 + min(entry.get("hits", 0), 5) * 0.05),  # hits 加成
                    importance=imp,
                )
                count += 1
            except (json.JSONDecodeError, Exception):
                continue
        logger.info("GMem import: 成功导入 %d 条 (source=%s)", count, source_tag)
        return count

    # ── 独立基准状态（冗余备份 #65） ──
    BASELINE_FILE = "mirror_baseline.json"

    def save_baseline(self, label: str = "auto", data_dir: str = ""):
        """持久化当前记忆状态为不可变基准。

        - 首次初始化时自动创建 baseline
        - 只有人类显式确认后才会更新
        - 回滚的终点是 baseline，不是上一个版本
        """
        import json
        from pathlib import Path

        base_path = Path(data_dir) if data_dir else Path(self._db_path).parent
        base_path.mkdir(parents=True, exist_ok=True)

        if self._conn is None:
            return

        snapshot = {
            "version": "1.0",
            "label": label,
            "created_at": time.time(),
            "memories": [],
        }
        cursor = self._conn.execute(
            "SELECT id, type, content, tags, source, strength, hits, verified, "
            "importance, created_at, last_access FROM memories WHERE is_active=1 "
            "ORDER BY importance DESC, strength DESC"
        )
        for row in cursor.fetchall():
            snapshot["memories"].append(
                {
                    "id": row[0],
                    "type": row[1],
                    "content": row[2],
                    "tags": row[3],
                    "source": row[4],
                    "strength": row[5],
                    "hits": row[6],
                    "verified": row[7],
                    "importance": row[8],
                    "created_at": row[9],
                    "last_access": row[10],
                }
            )

        filepath = base_path / f"{label}_{int(time.time())}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        logger.info("Mirror baseline saved: %s (%d memories)", filepath, len(snapshot["memories"]))
        return str(filepath)

    def rollback_to_baseline(self, data_dir: str = ""):
        """回滚到最近的人类确认基准，不是上一个版本。

        Restore memory state from the latest human-confirmed baseline.
        Falls back to the most recent baseline if no "human" baseline exists.
        """
        import json
        from pathlib import Path

        base_path = Path(data_dir) if data_dir else Path(self._db_path).parent
        if not base_path.exists():
            logger.warning("Baseline directory does not exist: %s", base_path)
            return False

        # Find baseline files: human label first, then auto
        candidates = sorted(base_path.glob("human_*.json"), reverse=True)
        if not candidates:
            candidates = sorted(base_path.glob("auto_*.json"), reverse=True)
        if not candidates:
            logger.warning("No baseline found in %s", base_path)
            return False

        baseline_path = candidates[0]
        try:
            with open(baseline_path, encoding="utf-8") as f:
                baseline = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load baseline %s: %s", baseline_path, e)
            return False

        if self._conn is None:
            return False

        # Restore: clear current memories and re-insert from baseline
        self._conn.execute("DELETE FROM memories")
        now = time.time()
        for mem in baseline.get("memories", []):
            self._conn.execute(
                "INSERT INTO memories (type, content, tags, source, strength, hits, verified, "
                "importance, created_at, last_access, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    mem.get("type", "lesson"),
                    mem["content"],
                    mem.get("tags", ""),
                    mem.get("source", "baseline_restore"),
                    mem.get("strength", 1.0),
                    mem.get("hits", 0),
                    mem.get("verified", 0),
                    mem.get("importance", 0.5),
                    mem.get("created_at", now),
                    now,
                ),
            )
        self._conn.commit()
        logger.info(
            "Rolled back to baseline %s (%d memories restored)",
            baseline_path.name,
            len(baseline.get("memories", [])),
        )
        return True

    def list_baselines(self, data_dir: str = ""):
        """列出所有可用的基准快照。"""
        from pathlib import Path

        base_path = Path(data_dir) if data_dir else Path(self._db_path).parent
        if not base_path.exists():
            return []
        results = []
        for fp in sorted(base_path.glob("*_*.json"), reverse=True):
            if "mirror_baseline" not in fp.name:
                continue
            try:
                import json

                with open(fp, encoding="utf-8") as f:
                    data = json.load(f)
                results.append(
                    {
                        "file": fp.name,
                        "label": data.get("label", ""),
                        "created_at": data.get("created_at", 0),
                        "memories": len(data.get("memories", [])),
                    }
                )
            except Exception:
                results.append({"file": fp.name, "error": True})
        return results

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


def _merge_content_if_better(conn, mem_id: int, new_content: str, new_tags: str, now: float):
    """When duplicate memories are found, update old records if new content is richer.
    Fully automated, never throws exceptions that affect the main flow."""
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
        logger.warning("Memory merge failed (non-blocking)", exc_info=True)
