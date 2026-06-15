#!/usr/bin/env python3
"""
认知新皮质 — 认知库存储层
=========================
基于 sqlite3，零外部依赖。
三层认知切片的读写、搜索、反馈、衰减。
"""

import json
import os
import sqlite3
from typing import List, Optional
from .schema import (
    CognitionSlice, CognitionType, FeedbackType, LayerSignal, LayerConcept, LayerStrategy,
    FEEDBACK_DELTA, now_iso
)


class CognitionStore:
    """认知库存储层"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        # 防御空 dirname（如仅传文件名 "cognition.db"）
        d = os.path.dirname(db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._lock = __import__('threading').Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.create_tables()

    def create_tables(self):
        """建表：三层结构 + 反馈记录"""
        cur = self._conn.cursor()

        # 认知切片表 — 含三层结构化字段
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cognition_slices (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                cognition_type  TEXT    NOT NULL,
                
                -- 信号层
                signal_keywords TEXT    DEFAULT '[]',      -- JSON List[str]
                signal_pattern  TEXT    DEFAULT '',
                
                -- 概念层
                concept_scene   TEXT    DEFAULT '',
                concept_agent   TEXT    DEFAULT '',
                concept_task    TEXT    DEFAULT '',
                
                -- 策略层
                strategy_lesson TEXT    DEFAULT '',
                strategy_agents TEXT    DEFAULT '[]',      -- JSON List[str]
                
                confidence      REAL    DEFAULT 0.5,
                access_count    INTEGER DEFAULT 0,
                created_at      TEXT    DEFAULT '',
                last_feedback   TEXT    DEFAULT '',
                source_log      TEXT    DEFAULT '',
                active          INTEGER DEFAULT 1
            )
        """)

        # 反馈记录表 
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cognition_feedback (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                slice_id        INTEGER NOT NULL,
                feedback_type   TEXT    NOT NULL,
                agent           TEXT    DEFAULT '',
                user_message    TEXT    DEFAULT '',
                created_at      TEXT    DEFAULT '',
                FOREIGN KEY (slice_id) REFERENCES cognition_slices(id)
            )
        """)

        # 索引
        cur.execute("CREATE INDEX IF NOT EXISTS idx_type ON cognition_slices(cognition_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_agent ON cognition_slices(concept_agent)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_active ON cognition_slices(active)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_confidence ON cognition_slices(confidence DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_slice ON cognition_feedback(slice_id)")

        self._conn.commit()

    # ── 写入 ──

    def save_slice(self, slice_data: CognitionSlice) -> int:
        """保存一条认知切片，返回 ID"""
        cur = self._conn.cursor()
        now = now_iso()

        sig = slice_data.signal_layer or LayerSignal([], "")
        con = slice_data.concept_layer or LayerConcept("", "", "")
        stra = slice_data.strategy_layer or LayerStrategy("", [])

        cur.execute("""
            INSERT INTO cognition_slices 
            (cognition_type, signal_keywords, signal_pattern,
             concept_scene, concept_agent, concept_task,
             strategy_lesson, strategy_agents,
             confidence, access_count, created_at, last_feedback, source_log)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, '', ?)
        """, (
            slice_data.cognition_type.value,
            json.dumps(sig.keywords, ensure_ascii=False),
            sig.pattern,
            con.scene, con.agent, con.task_type,
            stra.lesson,
            json.dumps(stra.applicable_agents, ensure_ascii=False),
            slice_data.confidence,
            now,
            slice_data.source_log,
        ))
        self._conn.commit()
        return cur.lastrowid

    # ── 查询 ──

    def get_slice(self, slice_id: int) -> Optional[CognitionSlice]:
        """按 ID 获取切片"""
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM cognition_slices WHERE id = ?", (slice_id,))
        row = cur.fetchone()
        return self._row_to_slice(row) if row else None

    def list_all(self) -> List[CognitionSlice]:
        """列出所有活跃切片"""
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM cognition_slices WHERE active=1 ORDER BY confidence DESC")
        return [self._row_to_slice(r) for r in cur.fetchall()]

    # ── 三层搜索 ──

    def search_by_signal(self, keywords: List[str] = None,
                         agent: str = "") -> List[CognitionSlice]:
        """信号层搜索：关键词匹配"""
        if not keywords:
            return []
        cur = self._conn.cursor()
        results = set()
        for kw in keywords:
            cur.execute("""
                SELECT * FROM cognition_slices 
                WHERE active=1 AND signal_keywords LIKE ? 
                AND (?='' OR concept_agent=?)
                ORDER BY confidence DESC LIMIT 5
            """, (f'%{kw}%', agent, agent))
            for r in cur.fetchall():
                results.add(r['id'])
        if not results:
            return []
        placeholders = ','.join('?' for _ in results)
        cur.execute(f"SELECT * FROM cognition_slices WHERE id IN ({placeholders}) ORDER BY confidence DESC", tuple(results))
        return [self._row_to_slice(r) for r in cur.fetchall()]

    def search_by_concept(self, scene: str = "", task_type: str = "",
                          agent: str = "") -> List[CognitionSlice]:
        """概念层搜索：同类场景匹配"""
        cur = self._conn.cursor()
        conditions = ["active=1"]
        params = []
        if scene:
            conditions.append("concept_scene LIKE ?")
            params.append(f'%{scene}%')
        if task_type:
            conditions.append("concept_task LIKE ?")
            params.append(f'%{task_type}%')
        if agent:
            conditions.append("(concept_agent=? OR strategy_agents LIKE ?)")
            params.extend([agent, f'%{agent}%'])
        sql = "SELECT * FROM cognition_slices WHERE " + " AND ".join(conditions)
        sql += " ORDER BY confidence DESC LIMIT 10"
        cur.execute(sql, params)
        return [self._row_to_slice(r) for r in cur.fetchall()]

    def search_by_strategy(self, lesson_fragment: str = "") -> List[CognitionSlice]:
        """策略层搜索：经验内容匹配"""
        if not lesson_fragment:
            return []
        cur = self._conn.cursor()
        cur.execute("""
            SELECT * FROM cognition_slices 
            WHERE active=1 AND strategy_lesson LIKE ? 
            ORDER BY confidence DESC LIMIT 10
        """, (f'%{lesson_fragment}%',))
        return [self._row_to_slice(r) for r in cur.fetchall()]

    def unified_search(self, query: str = "", agent: str = "",
                       min_confidence: float = 0.3) -> List[CognitionSlice]:
        """
        三层联合搜索
        
        1. 信号层：query 是否含认知库关键词
        2. 概念层：匹配同场景同 Agent
        3. 策略层：lesson 文本包含 query 片段
        """
        if not query:
            return []
        
        matched = {}
        
        # 信号层：截取 query 前 3 个字作为关键词片段
        for i in range(0, min(len(query), 12), 2):
            frag = query[i:i+4]
            if len(frag) < 2:
                continue
            for s in self.search_by_signal(keywords=[frag], agent=agent):
                if s.confidence >= min_confidence:
                    matched[s.id] = s
        
        # 概念层：按场景模糊匹配
        for k in ["写代码", "审计", "决策", "部署", "搜索", "调研", "写作"]:
            if k in query:
                for s in self.search_by_concept(scene=k, agent=agent):
                    if s.confidence >= min_confidence:
                        matched[s.id] = s
                break
        
        # 策略层：文本片段匹配
        for s in self.search_by_strategy(query[:30]):
            if s.confidence >= min_confidence:
                matched[s.id] = s
        
        # 按置信度排序
        result = sorted(matched.values(), key=lambda x: -x.confidence)
        return result[:10]

    # ── 反馈 ──

    def record_feedback(self, slice_id: int, feedback_type: FeedbackType,
                        agent: str = "", user_message: str = ""):
        """记录反馈并调整置信度"""
        now = now_iso()
        cur = self._conn.cursor()

        # 写反馈记录
        cur.execute("""
            INSERT INTO cognition_feedback (slice_id, feedback_type, agent, user_message, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (slice_id, feedback_type.value, agent, user_message, now))

        # 调整置信度
        delta = FEEDBACK_DELTA.get(feedback_type, 0)
        cur.execute("""
            UPDATE cognition_slices 
            SET confidence = MAX(0.1, MIN(1.0, confidence + ?)),
                last_feedback = ?
            WHERE id = ?
        """, (delta, feedback_type.value, slice_id))

        self._conn.commit()

    def increment_access(self, slice_ids):
        """批量增加访问计数"""
        if not slice_ids:
            return
        cur = self._conn.cursor()
        for sid in slice_ids:
            cur.execute("UPDATE cognition_slices SET access_count = access_count + 1 WHERE id = ?",
                        (sid,))
        self._conn.commit()

    def get_feedback_count(self, slice_id: int) -> int:
        """获取切片的反馈次数"""
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) FROM cognition_feedback WHERE slice_id = ?", (slice_id,))
        return cur.fetchone()[0]

    # ── 衰减 ──

    def decay(self, max_slices: int = 200):
        """
        衰减机制：超过 max_slices 时，删除最旧的已删除标记切片，
        然后降低访问次数最少的切片置信度。
        """
        cur = self._conn.cursor()

        # 统计活跃切片数
        cur.execute("SELECT COUNT(*) FROM cognition_slices WHERE active=1")
        count = cur.fetchone()[0]
        if count <= max_slices:
            return 0

        # 按 (confidence * 0.3 + access_count * 0.7) 排序，淘汰最低的
        excess = count - max_slices + 10  # 多淘汰一些留余量
        cur.execute("""
            SELECT id FROM cognition_slices WHERE active=1
            ORDER BY (confidence * 0.3 + CAST(access_count AS REAL) * 0.01) ASC, id ASC
            LIMIT ?
        """, (excess,))
        to_decay = [r['id'] for r in cur.fetchall()]

        for sid in to_decay:
            cur.execute("UPDATE cognition_slices SET confidence = MAX(0.1, confidence - 0.2), "
                        "active = CASE WHEN confidence <= 0.15 THEN 0 ELSE 1 END "
                        "WHERE id = ?", (sid,))

        self._conn.commit()
        return len(to_decay)

    # ── 工具方法 ──

    def _row_to_slice(self, row) -> CognitionSlice:
        """将 sqlite3.Row 转为 CognitionSlice"""
        return CognitionSlice(
            id=row['id'],
            cognition_type=CognitionType(row['cognition_type']),
            signal_layer=LayerSignal(
                keywords=json.loads(row['signal_keywords'] or '[]'),
                pattern=row['signal_pattern'] or '',
            ),
            concept_layer=LayerConcept(
                scene=row['concept_scene'] or '',
                agent=row['concept_agent'] or '',
                task_type=row['concept_task'] or '',
            ),
            strategy_layer=LayerStrategy(
                lesson=row['strategy_lesson'] or '',
                applicable_agents=json.loads(row['strategy_agents'] or '[]'),
            ),
            confidence=row['confidence'],
            access_count=row['access_count'],
            created_at=row['created_at'] or '',
            last_feedback=row['last_feedback'] or '',
            source_log=row['source_log'] or '',
        )

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
