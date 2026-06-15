#!/usr/bin/env python3
"""
认知新皮质 — 认知蒸馏器
======================
从学徒日志中提取三层认知切片，写入 cognition.db。

运行方式：
    python3 -m neocortex.distiller --distill
"""

import json
import re
from pathlib import Path

from .schema import ApprenticeLogEntry, CognitionSlice, CognitionType, LayerConcept, LayerSignal, LayerStrategy, now_iso
from .store import CognitionStore


class NeocortexDistiller:
    """
    认知蒸馏器：从学徒日志提炼三层认知切片

    distill() -> 读所有日志 -> 分析羽非反馈 -> 生成切片 -> 写库
    """

    # 场景到任务类型的映射（关键词匹配）
    SCENE_PATTERNS = [
        (r"写代码|代码|实现|开发|编程", "写代码"),
        (r"审计|审查|review|quality|质量", "审计"),
        (r"决策|选择|方案|选哪个|怎么", "决策"),
        (r"部署|发布|上线|push|deploy", "部署"),
        (r"搜索|查|调研|找资料|研究", "调研"),
        (r"写作|文章|文档|写东西|文案", "写作"),
    ]

    def __init__(self, apprentice_dir: str = None, db_path: str = None):
        self.apprentice_dir = apprentice_dir or "/opt/orange-arm-v2/apprentice/logs/"
        self.db_path = db_path or "/opt/orange-arm-v2/cognition_demo/data/cognition.db"
        self.store = CognitionStore(self.db_path)

    def distill(self) -> dict:
        """
        从学徒日志蒸馏认知切片

        返回统计信息。
        """
        logs = self._load_all_logs()
        if not logs:
            return {"slices_created": 0, "total_slices": 0, "type_distribution": {}}

        slices = []
        for entry in logs:
            extracted = self.distill_from_entry(entry)
            if extracted:
                slices.extend(extracted)

        # 去重：相同 strategy_lesson 的不重复写入
        saved = 0
        seen_lessons = set()
        for s in slices:
            key = s.strategy_layer.lesson[:30] if s.strategy_layer else ""
            if key and key in seen_lessons:
                continue
            if key:
                seen_lessons.add(key)
            # 检查是否已存在
            existing = self.store.search_by_strategy(s.strategy_layer.lesson[:20])
            if not existing:
                self.store.save_slice(s)
                saved += 1

        # 衰减
        self.store.decay(max_slices=200)

        # 统计
        all_slices = self.store.list_all()
        type_dist = {}
        for s in all_slices:
            k = s.cognition_type.value
            type_dist[k] = type_dist.get(k, 0) + 1

        return {
            "slices_created": saved,
            "total_slices": len(all_slices),
            "type_distribution": type_dist,
            "logs_processed": len(logs),
        }

    def distill_from_entry(self, entry: ApprenticeLogEntry) -> list[CognitionSlice]:
        """
        从一条学徒日志条目提取认知切片。

        提取策略：
        1. 如果有 user_feedback_raw → 优先用羽非的原话推断认知类型
        2. 如果有 reflection.lesson_learned → 提取 self_reflection
        3. 如果有 alternatives_considered → 提取决策模式
        """
        slices = []

        # 提取场景、Agent、任务类型
        agent = entry.meta.get("observer_id", "橘")
        scene = entry.scene.get("task_type", "")
        task_type = self._infer_task_type(scene)

        # 提取羽非反馈相关
        raw_feedback = entry.user_feedback_raw
        feedback_type = entry.user_feedback_type
        if not feedback_type and raw_feedback:
            feedback_type = self._infer_type_from_text(raw_feedback)

        # 提取反思教训
        reflection = entry.reflection or {}
        lesson_learned = reflection.get("lesson_learned", "")
        reflection.get("what_went_well", "")
        reflection.get("what_could_improve", "")

        # 提取决策理由
        thinking = entry.thinking or {}
        decision = thinking.get("decision_rationale", "")
        alternatives = thinking.get("alternatives_considered", [])

        # 提取用户反馈（output 里的）
        output = entry.output or {}
        user_feedback = output.get("user_feedback", "")

        # ── 情况1：有羽非原始反馈 → 生成认知切片 ──
        if raw_feedback or feedback_type or user_feedback:
            ctype = self._map_feedback_to_cognition(feedback_type)
            if ctype:
                # 信号层：从羽非反馈中提取关键词
                source = raw_feedback or user_feedback
                keywords = self._extract_keywords(source)
                signal = LayerSignal(keywords=keywords, pattern=source[:40])

                # 概念层
                concept = LayerConcept(scene=scene, agent=agent, task_type=task_type)

                # 策略层：教训
                lesson = lesson_learned or (f"羽非纠正：{source[:60]}")
                strategy = LayerStrategy(lesson=lesson, applicable_agents=[agent, "general"])

                slices.append(
                    CognitionSlice(
                        cognition_type=ctype,
                        signal_layer=signal,
                        concept_layer=concept,
                        strategy_layer=strategy,
                        confidence=0.7,  # 初始中等置信度
                        created_at=entry.meta.get("timestamp", now_iso()),
                        source_log=entry.meta.get("log_id", ""),
                    )
                )

        # ── 情况2：有教训反思 → 生成 self_reflection ──
        if lesson_learned:
            signal = LayerSignal(keywords=self._extract_keywords(lesson_learned), pattern=lesson_learned[:40])
            concept = LayerConcept(scene=scene, agent=agent, task_type=task_type)
            strategy = LayerStrategy(lesson=lesson_learned, applicable_agents=[agent])
            slices.append(
                CognitionSlice(
                    cognition_type=CognitionType.SELF_REFLECTION,
                    signal_layer=signal,
                    concept_layer=concept,
                    strategy_layer=strategy,
                    confidence=0.65,
                    created_at=entry.meta.get("timestamp", now_iso()),
                    source_log=entry.meta.get("log_id", ""),
                )
            )

        # ── 情况3：有决策比对 → 生成 TASK_PATTERN ──
        if decision and alternatives:
            signal = LayerSignal(keywords=["决策", "选择", "方案"], pattern=decision[:40])
            concept = LayerConcept(scene=scene, agent=agent, task_type=task_type)
            alt_text = "; ".join(a[:40] for a in alternatives)
            strategy = LayerStrategy(lesson=f"决策：{decision[:60]} | 比对：{alt_text[:60]}", applicable_agents=[agent])
            slices.append(
                CognitionSlice(
                    cognition_type=CognitionType.TASK_PATTERN,
                    signal_layer=signal,
                    concept_layer=concept,
                    strategy_layer=strategy,
                    confidence=0.6,
                    created_at=entry.meta.get("timestamp", now_iso()),
                    source_log=entry.meta.get("log_id", ""),
                )
            )

        return slices

    # ── 辅助方法 ──

    def _infer_task_type(self, scene: str) -> str:
        for pattern, ttype in self.SCENE_PATTERNS:
            if re.search(pattern, scene, re.IGNORECASE):
                return ttype
        return "通用"

    def _infer_type_from_text(self, text: str) -> str:
        """从文本推断反馈类型"""
        patterns = [
            ("direction_correction", r"不对|不是|错了|方向错|理解错|换一种|别|不要"),
            ("strategy_confirmation", r"对\b|可以|就这样|不错|好的|行\b"),
            ("priority_ruling", r"先做|优先|重点|核心|关键|更重要"),
            ("standard_judgment", r"深度|质量|不够|太浅|太粗|不行"),
            ("decision_delegation", r"你决定|你觉得|你来定|你怎么看"),
            ("output_adjustment", r"太长|简单|简洁|短|说重点"),
        ]
        for ftype, pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return ftype
        return ""

    def _map_feedback_to_cognition(self, feedback_type: str) -> CognitionType | None:
        mapping = {
            "direction_correction": CognitionType.DIRECTION_CORRECTION,
            "strategy_confirmation": CognitionType.STRATEGY_CONFIRMATION,
            "priority_ruling": CognitionType.PRIORITY_RULING,
            "standard_judgment": CognitionType.STANDARD_JUDGMENT,
            "decision_delegation": CognitionType.DECISION_DELEGATION,
            "output_adjustment": CognitionType.OUTPUT_ADJUSTMENT,
        }
        return mapping.get(feedback_type)

    def _extract_keywords(self, text: str) -> list[str]:
        """从文本提取关键词（中文2-4字词）"""
        if not text:
            return []
        # 简单切词：取2-4字片段
        words = []
        for i in range(len(text) - 1):
            chunk = text[i : i + 2]
            if all("\u4e00" <= c <= "\u9fff" for c in chunk):
                words.append(chunk)
        # 去重并取前5个
        seen = set()
        unique = []
        for w in words:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        return unique[:5]

    def _load_all_logs(self) -> list[ApprenticeLogEntry]:
        """加载所有学徒日志"""
        logs = []
        log_dir = Path(self.apprentice_dir)
        if not log_dir.exists():
            return logs

        for fpath in log_dir.glob("*.jsonl"):
            with open(fpath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        logs.append(
                            ApprenticeLogEntry(
                                schema=data.get("schema", ""),
                                meta=data.get("meta", {}),
                                scene=data.get("scene", {}),
                                input_data=data.get("input", {}),
                                thinking=data.get("thinking", {}),
                                action=data.get("action", {}),
                                output=data.get("output", {}),
                                reflection=data.get("reflection", {}),
                                user_feedback_raw=data.get("user_feedback_raw", ""),
                                user_feedback_type=data.get("user_feedback_type", ""),
                                user_sentiment=data.get("user_sentiment", ""),
                                scene_context=data.get("scene_context", ""),
                            )
                        )
                    except json.JSONDecodeError:
                        continue

        return logs


# ── 可执行入口 ──
if __name__ == "__main__":
    import sys

    distiller = NeocortexDistiller()

    if "--distill" in sys.argv:
        result = distiller.distill()
        print("蒸馏完成:")
        print(f"  处理日志: {result['logs_processed']} 条")
        print(f"  新增切片: {result['slices_created']} 条")
        print(f"  当前总数: {result['total_slices']} 条")
        print(f"  类型分布: {result['type_distribution']}")
    else:
        print("用法: python3 -m neocortex.distiller --distill")
        print(f"学徒日志: {distiller.apprentice_dir}")
        print(f"认知库: {distiller.db_path}")
