#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
╔═══════════════════════════════════════════════════════════╗
║  Cognifold — Active memory architecture                                  ║
║  arxiv: 2605.13438                                        ║
║                                                           ║
║  Three-Layer Cognitive Structure (extending CLS theory):                              ║
║    Layer 1 (Hippocampus)  = mirror memory store                    ║
║    Layer 2 (Neocortex)    = concept cluster self-org                        ║
║    Layer 3 (Prefrontal)   = intent emergence + decision                     ║
║                                                           ║
║  Core mechanisms:                                                  ║
║    - Event stream → semantic similarity merge → decay → associative recall re-linking         ║
║    - Cluster density exceeds threshold → active intent emergence                        ║
║    - Not "store and retrieve", but "memories organize themselves"               ║
║                                                           ║
║  Usage:                                                      ║
║    from lib.cognifold import Cognifold                     ║
║    cf = Cognifold(mirror_instance)                         ║
║    cf.on_record(content, mtype, tags)  # triggers organization on record     ║
║    intents = cf.check_intents()         # check emerged intents   ║
╚═══════════════════════════════════════════════════════════╝
"""

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

# === Configuration ===
DATA_DIR = Path(os.getenv("GBASE_COGNIFOLD_DIR", "./data"))
COGNIFOLD_DB = DATA_DIR / "cognifold.db"

# Concept cluster parameters
CLUSTER_WINDOW_DAYS = 7  # Concept cluster time window
CLUSTER_DENSITY_THRESHOLD = 5  # Same concept ≥5 events in 7 days triggers intent emergence
MIN_CLUSTER_SIZE = 3  # Minimum cluster size
SIMILARITY_JACCARD = 0.35  # Jaccard similarity threshold (merge threshold)

# Intent emergence
INTENT_LEVELS = ["notice", "suggestion", "alert"]
MAX_INTENTS_PER_CHECK = 3  # Max 3 intents emerged per check

# Stop words (Chinese)
STOP_WORDS = {
    "的",
    "了",
    "在",
    "是",
    "我",
    "有",
    "和",
    "就",
    "不",
    "人",
    "都",
    "一",
    "一个",
    "上",
    "也",
    "很",
    "到",
    "说",
    "要",
    "去",
    "你",
    "会",
    "着",
    "没有",
    "看",
    "好",
    "自己",
    "这",
    "他",
    "她",
    "它",
    "们",
    "那",
    "什么",
    "怎么",
    "可以",
    "就是",
    "不是",
    "这个",
    "那个",
    "因为",
    "所以",
    "但是",
    "如果",
    "虽然",
    "而且",
    "或者",
    "不过",
    "已经",
    "还是",
    "只是",
    "然后",
    "可能",
}


class Cognifold:
    """Active memory architecture — concept cluster self-organization + intent emergence.

    Architecture:
      - Layer 1: Event stream ingestion (receives mirror events via on_record)
      - Layer 2: Concept cluster self-organization (semantic similarity merge, decay, re-linking)
      - Layer 3: Intent emergence (density detection + triggers)
    """

    def __init__(self, mirror_instance=None):
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        self._mirror = mirror_instance
        self._conn = sqlite3.connect(str(COGNIFOLD_DB), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._setup_schema()

        # In-memory cache: concept → cluster mapping (for faster queries)
        self._concept_cache: dict[str, int] = {}
        self._last_intent_check = 0.0

    def _setup_schema(self):
        """Initialize cognitive structure tables."""
        # Concepts table (Layer 2)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS concepts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                cluster_id INTEGER,
                weight REAL DEFAULT 1.0,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                total_events INTEGER DEFAULT 1,
                decay_rate REAL DEFAULT 0.95
            )
        """)

        # Concept clusters table (Layer 2)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS clusters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                concept_count INTEGER DEFAULT 0,
                event_count INTEGER DEFAULT 0,
                density REAL DEFAULT 0.0,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                is_active INTEGER DEFAULT 1,
                last_decay REAL DEFAULT 0
            )
        """)

        # Concept links table
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS concept_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                concept_a_id INTEGER NOT NULL,
                concept_b_id INTEGER NOT NULL,
                co_occurrence INTEGER DEFAULT 1,
                strength REAL DEFAULT 0.5,
                FOREIGN KEY (concept_a_id) REFERENCES concepts(id),
                FOREIGN KEY (concept_b_id) REFERENCES concepts(id)
            )
        """)

        # Intent emergence log (Layer 3)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id INTEGER NOT NULL,
                level TEXT DEFAULT 'notice',
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                evidence TEXT,
                emerged_at REAL NOT NULL,
                acknowledged INTEGER DEFAULT 0,
                acted_upon INTEGER DEFAULT 0,
                FOREIGN KEY (cluster_id) REFERENCES clusters(id)
            )
        """)

        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_concepts_cluster ON concepts(cluster_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_clusters_active ON clusters(is_active, density DESC)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_links_concept ON concept_links(concept_a_id)")

        self._conn.commit()

    # ─── Layer 1: Event Ingestion ─────────────────────────

    def on_record(self, content: str, _mtype: str = "", tags: list[str] = None, _source: str = ""):
        """Entry point for processing new memory events.

        This is the hook connecting Cognifold to mirror.record().
        Called each time mirror records a new memory.
        """
        tags = tags or []
        now = time.time()

        # Extract concepts
        concepts = self._extract_concepts(content, tags)

        if not concepts:
            return

        # Update concept stats
        concept_ids = []
        for concept_name in concepts:
            cid = self._upsert_concept(concept_name, now)
            if cid:
                concept_ids.append(cid)

        # Update co-occurrence relationships
        self._update_co_occurrence(concept_ids, now)

        # Recalculate cluster density
        self._recluster(concept_ids, now)

        # Periodic decay
        self._decay_if_needed(now)

    def _extract_concepts(self, content: str, tags: list[str]) -> list[str]:
        """Extract concept keywords from content.

        Strategy:
          1. Chinese word segmentation (simple regex + stop word filtering)
          2. Extract 2-4 character words
          3. Merge words from tags
        """
        concepts = set()

        # Extract from content
        cleaned = re.sub(r"[^\u4e00-\u9fff\w]", " ", content)
        words = cleaned.split()

        for word in words:
            word = word.strip()
            # Chinese words: 2-4 chars
            if re.match(r"^[\u4e00-\u9fff]{2,4}$", word):
                if word not in STOP_WORDS:
                    concepts.add(word)
            # English tech terms
            elif re.match(r"^[A-Z][a-zA-Z]+$", word) and len(word) >= 3:
                concepts.add(word.lower())

        # Merge from tags
        for tag in tags:
            tag = tag.strip().lower()
            if tag and len(tag) >= 2 and tag not in STOP_WORDS:
                concepts.add(tag)

        return list(concepts)[:10]  # Max 10 concepts

    # ─── Layer 2: Concept Cluster Self-Organization ───────

    def _upsert_concept(self, name: str, now: float) -> int | None:
        """Update or create concept."""
        if name in self._concept_cache:
            cid = self._concept_cache[name]
            self._conn.execute("UPDATE concepts SET last_seen=?, total_events=total_events+1 WHERE id=?", (now, cid))
            return cid

        cursor = self._conn.execute("SELECT id FROM concepts WHERE name=?", (name,))
        row = cursor.fetchone()
        if row:
            cid = row[0]
            self._conn.execute("UPDATE concepts SET last_seen=?, total_events=total_events+1 WHERE id=?", (now, cid))
            self._concept_cache[name] = cid
            return cid

        cursor = self._conn.execute(
            "INSERT INTO concepts (name, first_seen, last_seen) VALUES (?, ?, ?)", (name, now, now)
        )
        cid = cursor.lastrowid
        self._concept_cache[name] = cid
        self._conn.commit()
        return cid

    def _update_co_occurrence(self, concept_ids: list[int], _now: float):
        """Update concept co-occurrence matrix."""
        for i in range(len(concept_ids)):
            for j in range(i + 1, len(concept_ids)):
                a, b = sorted([concept_ids[i], concept_ids[j]])
                cursor = self._conn.execute(
                    "SELECT id, co_occurrence, strength FROM concept_links WHERE concept_a_id=? AND concept_b_id=?",
                    (a, b),
                )
                row = cursor.fetchone()
                if row:
                    new_strength = min(1.0, row[2] + 0.1)
                    self._conn.execute(
                        "UPDATE concept_links SET co_occurrence=co_occurrence+1, strength=? WHERE id=?",
                        (new_strength, row[0]),
                    )
                else:
                    self._conn.execute("INSERT INTO concept_links (concept_a_id, concept_b_id) VALUES (?, ?)", (a, b))
        self._conn.commit()

    def _recluster(self, triggered_concept_ids: list[int], now: float):
        """Reorganize concept clusters.

        Algorithm:
          1. Use triggered concepts as seeds
          2. Find strongly related concepts via co-occurrence (strength >= SIMILARITY_JACCARD)
          3. Merge overlapping clusters
          4. Update cluster density
        """
        if not triggered_concept_ids:
            return

        # Load all active concepts (within 7-day window)
        cutoff = now - (CLUSTER_WINDOW_DAYS * 86400)
        cursor = self._conn.execute(
            "SELECT id, name, cluster_id, total_events FROM concepts WHERE last_seen >= ?", (cutoff,)
        )
        all_concepts = {row[0]: {"name": row[1], "cluster_id": row[2], "events": row[3]} for row in cursor.fetchall()}

        if not all_concepts:
            return

        # Find neighbors for each triggered concept
        for seed_id in triggered_concept_ids:
            if seed_id not in all_concepts:
                continue

            # BFS to find related concepts
            cluster_members = {seed_id}
            queue = [seed_id]
            while queue:
                cid = queue.pop(0)
                cursor = self._conn.execute(
                    "SELECT concept_a_id, concept_b_id, strength FROM concept_links "
                    "WHERE (concept_a_id=? OR concept_b_id=?) AND strength >= ?",
                    (cid, cid, SIMILARITY_JACCARD),
                )
                for row in cursor.fetchall():
                    neighbor = row[0] if row[0] != cid else row[1]
                    if neighbor in all_concepts and neighbor not in cluster_members:
                        cluster_members.add(neighbor)
                        queue.append(neighbor)

            if len(cluster_members) < MIN_CLUSTER_SIZE:
                continue

            # Create or update cluster
            event_count = sum(all_concepts[c]["events"] for c in cluster_members)
            density = event_count / CLUSTER_WINDOW_DAYS  # Events per day density

            # Pick most representative concept name as cluster label
            label = self._pick_cluster_label(cluster_members, all_concepts)

            # Check if merging with existing cluster
            existing_cluster_id = self._find_merge_target(cluster_members, all_concepts)

            if existing_cluster_id:
                cluster_id = existing_cluster_id
                self._conn.execute(
                    "UPDATE clusters SET concept_count=?, event_count=?, density=?, last_seen=? WHERE id=?",
                    (len(cluster_members), event_count, density, now, cluster_id),
                )
            else:
                cursor = self._conn.execute(
                    "INSERT INTO clusters (label, concept_count, event_count, "
                    "density, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                    (label, len(cluster_members), event_count, density, now, now),
                )
                cluster_id = cursor.lastrowid

            # Update concept cluster assignment
            for cid in cluster_members:
                self._conn.execute("UPDATE concepts SET cluster_id=? WHERE id=?", (cluster_id, cid))
                all_concepts[cid]["cluster_id"] = cluster_id

        self._conn.commit()

        # Update memory cache
        for cid, info in all_concepts.items():
            if info["cluster_id"]:
                self._concept_cache[info["name"]] = cid

    def _pick_cluster_label(self, member_ids: set[int], all_concepts: dict) -> str:
        """Pick most representative concept name as cluster label."""
        candidates = [(all_concepts[c]["events"], all_concepts[c]["name"]) for c in member_ids if c in all_concepts]
        candidates.sort(reverse=True)
        if not candidates:
            return "unknown"
        top = candidates[0][1]
        # If there is a runner-up, use "top + runner-up" as label
        if len(candidates) >= 2:
            return f"{top}/{candidates[1][1]}"
        return top

    def _find_merge_target(self, member_ids: set[int], all_concepts: dict) -> int | None:
        """Find existing cluster to merge into."""
        cluster_ids = set()
        for cid in member_ids:
            if cid in all_concepts and all_concepts[cid]["cluster_id"]:
                cluster_ids.add(all_concepts[cid]["cluster_id"])

        if len(cluster_ids) == 1:
            return cluster_ids.pop()
        return None

    def _decay_if_needed(self, now: float):
        """Periodically decay expired concepts."""
        if now - self._last_intent_check < 3600:  # Check once per hour
            return

        cutoff = now - (CLUSTER_WINDOW_DAYS * 2 * 86400)
        self._conn.execute("UPDATE concepts SET weight=weight*decay_rate WHERE last_seen < ?", (cutoff,))
        self._conn.execute("UPDATE clusters SET is_active=0 WHERE last_seen < ?", (cutoff,))
        self._conn.commit()
        self._last_intent_check = now

    # ─── Layer 3: Intent Emergence ────────────────────────

    def check_intents(self) -> list[dict[str, Any]]:
        """Check for intents that need to emerge.

        When a concept cluster density exceeds threshold, auto-generate and push intents.

        Returns:
            List of emerged intents, each containing {level, title, body, evidence}
        """
        now = time.time()
        cutoff = now - (CLUSTER_WINDOW_DAYS * 86400)

        # Find high-density active clusters
        cursor = self._conn.execute(
            "SELECT c.id, c.label, c.concept_count, c.event_count, c.density, "
            "c.first_seen, c.last_seen "
            "FROM clusters c "
            "WHERE c.is_active=1 AND c.density >= ? AND c.last_seen >= ? "
            "ORDER BY c.density DESC LIMIT ?",
            (CLUSTER_DENSITY_THRESHOLD, cutoff, MAX_INTENTS_PER_CHECK),
        )

        clusters = cursor.fetchall()
        if not clusters:
            return []

        intents = []
        for row in clusters:
            cluster_id, label, concept_count, event_count, density, first_seen, last_seen = row

            # Check if same intent already emerged (dedup)
            existing = self._conn.execute(
                "SELECT id FROM intents WHERE cluster_id=? AND emerged_at > ?", (cluster_id, now - 86400 * 3)
            ).fetchone()
            if existing:
                continue

            # Generate intent
            intent = self._generate_intent(
                cluster_id, label, concept_count, event_count, density, first_seen, last_seen
            )
            if intent:
                intents.append(intent)

        self._conn.commit()
        return intents

    def _generate_intent(
        self,
        cluster_id: int,
        label: str,
        concept_count: int,
        event_count: int,
        density: float,
        first_seen: float,
        last_seen: float,
    ) -> dict[str, Any] | None:
        """Generate intent based on cluster density.

        Intent levels:
          - notice:     density 5-10,   heads-up
          - suggestion: density 10-20,  recommended action
          - alert:      density > 20,   urgent alert
        """
        now = time.time()

        # Determine intent level
        if density > 20:
            level = "alert"
        elif density > 10:
            level = "suggestion"
        else:
            level = "notice"

        # Generate title and body
        days_span = max(1, int((last_seen - first_seen) / 86400))

        if level == "alert":
            title = f"🚨 High-Density Concept Cluster: {label}"
            body = (
                f"In the past {days_span} days, '{label}' related event density reached {density:.1f}/day "
                f"({event_count} events total, {concept_count} related concepts). "
                f"Immediate review and response strategy recommended."
            )
        elif level == "suggestion":
            title = f"💡 Emerging Pattern: {label}"
            body = (
                f"'{label}' related events appeared {event_count} times in {days_span} days "
                f"({concept_count} related concepts), density {density:.1f}/day. "
                f"Consider incorporating this pattern into formal rules or workflows."
            )
        else:
            title = f"📌 Notice: {label}"
            body = (
                f"'{label}' concept cluster accumulated {event_count} events in {days_span} days "
                f"(density {density:.1f}/day). Worth monitoring its development trend."
            )

        # Collect evidence (related concepts)
        cursor = self._conn.execute(
            "SELECT name FROM concepts WHERE cluster_id=? ORDER BY total_events DESC LIMIT 5", (cluster_id,)
        )
        evidence_concepts = [row[0] for row in cursor.fetchall()]
        evidence = f"Related concepts: {', '.join(evidence_concepts)}"

        # Store intent
        cursor = self._conn.execute(
            "INSERT INTO intents (cluster_id, level, title, body, evidence, emerged_at) VALUES (?, ?, ?, ?, ?, ?)",
            (cluster_id, level, title, body, evidence, now),
        )
        intent_id = cursor.lastrowid

        return {
            "id": intent_id,
            "cluster_id": cluster_id,
            "level": level,
            "title": title,
            "body": body,
            "evidence": evidence,
            "emerged_at": now,
        }

    def acknowledge_intent(self, intent_id: int) -> bool:
        """Mark intent as acknowledged."""
        self._conn.execute("UPDATE intents SET acknowledged=1 WHERE id=?", (intent_id,))
        self._conn.commit()
        return True

    def act_on_intent(self, intent_id: int) -> bool:
        """Mark intent as acted upon."""
        self._conn.execute("UPDATE intents SET acted_upon=1 WHERE id=?", (intent_id,))
        self._conn.commit()
        return True

    # ─── Stats & Maintenance ──────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return Cognifold statistics."""
        concept_count = self._conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
        active_concepts = self._conn.execute("SELECT COUNT(*) FROM concepts WHERE weight > 0.1").fetchone()[0]
        cluster_count = self._conn.execute("SELECT COUNT(*) FROM clusters WHERE is_active=1").fetchone()[0]
        intent_count = self._conn.execute("SELECT COUNT(*) FROM intents").fetchone()[0]
        unacknowledged = self._conn.execute("SELECT COUNT(*) FROM intents WHERE acknowledged=0").fetchone()[0]

        # Current high-density clusters
        cursor = self._conn.execute(
            "SELECT label, round(density,1) FROM clusters "
            "WHERE is_active=1 AND density >= ? ORDER BY density DESC LIMIT 5",
            (CLUSTER_DENSITY_THRESHOLD,),
        )
        top_clusters = [{"label": r[0], "density": r[1]} for r in cursor.fetchall()]

        return {
            "total_concepts": concept_count,
            "active_concepts": active_concepts,
            "active_clusters": cluster_count,
            "total_intents": intent_count,
            "unacknowledged_intents": unacknowledged,
            "top_clusters": top_clusters,
        }

    def close(self):
        if self._conn:
            self._conn.close()


# ─── Convenience Functions ────────────────────────────

_cognifold_instance: Cognifold | None = None


def get_cognifold(mirror_instance=None) -> Cognifold:
    global _cognifold_instance
    if _cognifold_instance is None:
        _cognifold_instance = Cognifold(mirror_instance)
    return _cognifold_instance


if __name__ == "__main__":
    import sys

    cf = Cognifold()
    if "--stats" in sys.argv:
        print(json.dumps(cf.stats(), ensure_ascii=False, indent=2))
    elif "--intents" in sys.argv:
        intents = cf.check_intents()
        print(json.dumps(intents, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(cf.stats(), ensure_ascii=False, indent=2))
