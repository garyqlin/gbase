#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
╔═══════════════════════════════════════════════════════════╗
║  Cognifold — 主动记忆架构                                  ║
║  arxiv: 2605.13438                                        ║
║                                                           ║
║  三层认知结构（扩展 CLS 理论）:                              ║
║    Layer 1 (海马体)    = mirror 记忆存储                    ║
║    Layer 2 (新皮层)    = 概念簇自组织                        ║
║    Layer 3 (前额叶)    = 意图浮现 + 决策                     ║
║                                                           ║
║  核心机制:                                                  ║
║    - 事件流 → 语义相似合并 → 过期衰减 → 关联回忆重链         ║
║    - 概念簇密度超阈值 → 主动浮现意图                        ║
║    - 不是"存好了等检索"，而是"记忆自动组织自己"               ║
║                                                           ║
║  用法:                                                      ║
║    from lib.cognifold import Cognifold                     ║
║    cf = Cognifold(mirror_instance)                         ║
║    cf.on_record(content, mtype, tags)  # 记录时触发组织     ║
║    intents = cf.check_intents()         # 检查浮现的意图   ║
╚═══════════════════════════════════════════════════════════╝
"""

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

# === 配置 ===
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
COGNIFOLD_DB = DATA_DIR / "cognifold.db"

# 概念簇参数
CLUSTER_WINDOW_DAYS = 7  # 概念簇时间窗口
CLUSTER_DENSITY_THRESHOLD = 5  # 同一概念 7 天内 ≥5 条触发意图浮现
MIN_CLUSTER_SIZE = 3  # 最小聚类大小
SIMILARITY_JACCARD = 0.35  # Jaccard 相似度阈值（合并阈值）

# 意图浮现
INTENT_LEVELS = ["notice", "suggestion", "alert"]
MAX_INTENTS_PER_CHECK = 3  # 每次检查最多浮现 3 个意图

# 停用词（中文）
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
    """主动记忆架构 — 概念簇自组织 + 意图浮现。

    架构:
      - Layer 1: 事件流接入（通过 on_record 接收 mirror 事件）
      - Layer 2: 概念簇自组织（语义相似合并、衰减、重链）
      - Layer 3: 意图浮现（密度检测 + 触发器）
    """

    def __init__(self, mirror_instance=None):
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        self._mirror = mirror_instance
        self._conn = sqlite3.connect(str(COGNIFOLD_DB), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._setup_schema()

        # 内存缓存：概念→簇映射（加速查询）
        self._concept_cache: dict[str, int] = {}
        self._last_intent_check = 0.0

    def _setup_schema(self):
        """初始化认知结构表。"""
        # 概念表（Layer 2）
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

        # 概念簇表（Layer 2）
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

        # 概念关联表
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

        # 意图浮现日志（Layer 3）
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

    # ─── Layer 1: 事件接入 ────────────────────────────────

    def on_record(self, content: str, mtype: str = "", tags: list[str] = None, source: str = ""):
        """接收到新记忆事件时的处理入口。

        这是 Cognifold 连接 mirror.record() 的钩子。
        每次 mirror 记录新记忆时调用此方法。
        """
        tags = tags or []
        now = time.time()

        # 提取概念
        concepts = self._extract_concepts(content, tags)

        if not concepts:
            return

        # 更新概念统计
        concept_ids = []
        for concept_name in concepts:
            cid = self._upsert_concept(concept_name, now)
            if cid:
                concept_ids.append(cid)

        # 更新共现关系
        self._update_co_occurrence(concept_ids, now)

        # 重新计算簇密度
        self._recluster(concept_ids, now)

        # 定期衰减
        self._decay_if_needed(now)

    def _extract_concepts(self, content: str, tags: list[str]) -> list[str]:
        """从内容中提取概念关键词。

        策略:
          1. 中文分词（简单正则 + 停用词过滤）
          2. 提取 2-4 字词
          3. 合并 tags 中的词
        """
        concepts = set()

        # 从内容提取
        cleaned = re.sub(r"[^\u4e00-\u9fff\w]", " ", content)
        words = cleaned.split()

        for word in words:
            word = word.strip()
            # 中文词：2-4 字
            if re.match(r"^[\u4e00-\u9fff]{2,4}$", word):
                if word not in STOP_WORDS:
                    concepts.add(word)
            # 英文技术词
            elif re.match(r"^[A-Z][a-zA-Z]+$", word) and len(word) >= 3:
                concepts.add(word.lower())

        # 从 tags 合并
        for tag in tags:
            tag = tag.strip().lower()
            if tag and len(tag) >= 2 and tag not in STOP_WORDS:
                concepts.add(tag)

        return list(concepts)[:10]  # 最多 10 个概念

    # ─── Layer 2: 概念簇自组织 ────────────────────────────

    def _upsert_concept(self, name: str, now: float) -> int | None:
        """更新或创建概念。"""
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

    def _update_co_occurrence(self, concept_ids: list[int], now: float):
        """更新概念共现矩阵（带超时保护：最大2秒）。"""
        import time as _t
        _start = _t.time()
        _MAX_MS = 2000
        for i in range(len(concept_ids)):
            if _t.time() - _start > _MAX_MS / 1000:
                import logging as _lg
                _lg.getLogger(__name__).warning(
                    "Cognifold co-occurrence timeout after %d/%d concepts", i, len(concept_ids)
                )
                break
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
        """重新组织概念簇。

        算法:
          1. 以触发的概念为种子
          2. 通过共现关系找到强关联概念（strength >= SIMILARITY_JACCARD）
          3. 合并重叠簇
          4. 更新簇密度
        """
        if not triggered_concept_ids:
            return

        # 加载所有活跃概念（7 天窗口内）
        cutoff = now - (CLUSTER_WINDOW_DAYS * 86400)
        cursor = self._conn.execute(
            "SELECT id, name, cluster_id, total_events FROM concepts WHERE last_seen >= ?", (cutoff,)
        )
        all_concepts = {row[0]: {"name": row[1], "cluster_id": row[2], "events": row[3]} for row in cursor.fetchall()}

        if not all_concepts:
            return

        # 找每个触发概念的邻居
        for seed_id in triggered_concept_ids:
            if seed_id not in all_concepts:
                continue

            # BFS 找关联概念
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

            # 创建或更新簇
            event_count = sum(all_concepts[c]["events"] for c in cluster_members)
            density = event_count / CLUSTER_WINDOW_DAYS  # 每天事件密度

            # 找最代表性的概念名作为簇标签
            label = self._pick_cluster_label(cluster_members, all_concepts)

            # 检查是否与现有簇合并
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

            # 更新概念的簇归属
            for cid in cluster_members:
                self._conn.execute("UPDATE concepts SET cluster_id=? WHERE id=?", (cluster_id, cid))
                all_concepts[cid]["cluster_id"] = cluster_id

        self._conn.commit()

        # 更新内存缓存
        for cid, info in all_concepts.items():
            if info["cluster_id"]:
                self._concept_cache[info["name"]] = cid

    def _pick_cluster_label(self, member_ids: set[int], all_concepts: dict) -> str:
        """选最具代表性的概念名作为簇标签。"""
        candidates = [(all_concepts[c]["events"], all_concepts[c]["name"]) for c in member_ids if c in all_concepts]
        candidates.sort(reverse=True)
        if not candidates:
            return "unknown"
        top = candidates[0][1]
        # 如果还有第二名，用 "top + 第二名" 做标签
        if len(candidates) >= 2:
            return f"{top}/{candidates[1][1]}"
        return top

    def _find_merge_target(self, member_ids: set[int], all_concepts: dict) -> int | None:
        """找可以合并的现有簇。"""
        cluster_ids = set()
        for cid in member_ids:
            if cid in all_concepts and all_concepts[cid]["cluster_id"]:
                cluster_ids.add(all_concepts[cid]["cluster_id"])

        if len(cluster_ids) == 1:
            return cluster_ids.pop()
        return None

    def _decay_if_needed(self, now: float):
        """定期衰减过期概念。"""
        if now - self._last_intent_check < 3600:  # 每小时检查一次
            return

        cutoff = now - (CLUSTER_WINDOW_DAYS * 2 * 86400)
        self._conn.execute("UPDATE concepts SET weight=weight*decay_rate WHERE last_seen < ?", (cutoff,))
        self._conn.execute("UPDATE clusters SET is_active=0 WHERE last_seen < ?", (cutoff,))
        self._conn.commit()
        self._last_intent_check = now

    # ─── Layer 3: 意图浮现 ────────────────────────────────

    def check_intents(self) -> list[dict[str, Any]]:
        """检查是否有需要浮现的意图。

        当概念簇密度超过阈值时，自动生成意图并推送。

        Returns:
            浮现的意图列表，每个包含 {level, title, body, evidence}
        """
        now = time.time()
        cutoff = now - (CLUSTER_WINDOW_DAYS * 86400)

        # 查找高密度活跃簇
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

            # 检查是否已浮现过相同意图（去重）
            existing = self._conn.execute(
                "SELECT id FROM intents WHERE cluster_id=? AND emerged_at > ?", (cluster_id, now - 86400 * 3)
            ).fetchone()
            if existing:
                continue

            # 生成意图
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
        """基于簇密度生成意图。

        意图级别:
          - notice:   density 5-10,   提示注意
          - suggestion: density 10-20, 建议行动
          - alert:    density > 20,    紧急告警
        """
        now = time.time()

        # 确定意图级别
        if density > 20:
            level = "alert"
        elif density > 10:
            level = "suggestion"
        else:
            level = "notice"

        # 生成标题和正文
        days_span = max(1, int((last_seen - first_seen) / 86400))

        if level == "alert":
            title = f"🚨 高密度概念簇: {label}"
            body = (
                f"过去 {days_span} 天内，'{label}' 相关事件密度达到 {density:.1f}/天 "
                f"（共 {event_count} 条，{concept_count} 个关联概念）。"
                f"建议立即审查并制定应对策略。"
            )
        elif level == "suggestion":
            title = f"💡 浮现模式: {label}"
            body = (
                f"'{label}' 相关事件在 {days_span} 天内出现了 {event_count} 次 "
                f"（{concept_count} 个关联概念），密度 {density:.1f}/天。"
                f"建议考虑将此模式纳入正式规则或流程。"
            )
        else:
            title = f"📌 注意: {label}"
            body = (
                f"'{label}' 概念簇在 {days_span} 天内累积了 {event_count} 条事件 "
                f"（密度 {density:.1f}/天）。值得关注其发展趋势。"
            )

        # 收集证据（关联的概念）
        cursor = self._conn.execute(
            "SELECT name FROM concepts WHERE cluster_id=? ORDER BY total_events DESC LIMIT 5", (cluster_id,)
        )
        evidence_concepts = [row[0] for row in cursor.fetchall()]
        evidence = f"关联概念: {', '.join(evidence_concepts)}"

        # 存储意图
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
        """标记意图为已确认。"""
        self._conn.execute("UPDATE intents SET acknowledged=1 WHERE id=?", (intent_id,))
        self._conn.commit()
        return True

    def act_on_intent(self, intent_id: int) -> bool:
        """标记意图为已处理。"""
        self._conn.execute("UPDATE intents SET acted_upon=1 WHERE id=?", (intent_id,))
        self._conn.commit()
        return True

    # ─── 统计与维护 ────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """返回 Cognifold 统计信息。"""
        concept_count = self._conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
        active_concepts = self._conn.execute("SELECT COUNT(*) FROM concepts WHERE weight > 0.1").fetchone()[0]
        cluster_count = self._conn.execute("SELECT COUNT(*) FROM clusters WHERE is_active=1").fetchone()[0]
        intent_count = self._conn.execute("SELECT COUNT(*) FROM intents").fetchone()[0]
        unacknowledged = self._conn.execute("SELECT COUNT(*) FROM intents WHERE acknowledged=0").fetchone()[0]

        # 当前高密度簇
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


# ─── 便捷函数 ───────────────────────────────────────────

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
