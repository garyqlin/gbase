#!/usr/bin/env python3
"""
认知新皮质 — 认知扫描器（三层扫描）
=============================
信号层 → 概念层 → 策略层 逐级扫描。
"""

import re

from .schema import CognitionSlice, CognitionType
from .store import CognitionStore

# 信号层：认知类型 => 正则模式
SIGNAL_PATTERNS = {
    CognitionType.DIRECTION_CORRECTION: re.compile(
        r"(?i)(不对|不是|错了|方向错|理解错|你错了|不是.*意思|"
        r"不要.*这样|换一种|重新.*来|换方向|换个想法|重来|别这么干|别做|别给)"
    ),
    CognitionType.STRATEGY_CONFIRMATION: re.compile(
        r"(?i)(对\b|对[的得]|没错|正是|方向是对|"
        r"可以\b|就这样|挺好\b|不错\b|赞同|同意|认可|好的\b|就这么办)"
    ),
    CognitionType.PRIORITY_RULING: re.compile(
        r"(?i)(先做|先别|优先|重点|核心|关键|"
        r"这个更重要|放着|不急|后面再说|首要|次要|最重要的)"
    ),
    CognitionType.STANDARD_JUDGMENT: re.compile(
        r"(?i)(深度不够|质量不够|不够好|不够深|"
        r"太浅|太粗糙|太简单|太表面|不行|差远了|质量差|品质不够)"
    ),
    CognitionType.DECISION_DELEGATION: re.compile(
        r"(?i)(你决定|你来定|你觉得呢|你怎么看|"
        r"你来拿主意|你来判断|你自己看|方案你来|你来出方案)"
    ),
    CognitionType.OUTPUT_ADJUSTMENT: re.compile(
        r"(?i)(太长了|简单说|简洁|短一点|太长|"
        r"说重点|废话太多|能不能短|一句话说|精简|别写那么多|简短)"
    ),
    CognitionType.TASK_PATTERN: re.compile(r"(?i)(帮我|查一下|看看|研究|分析|怎么|如何|什么方式|步骤|流程)"),
}


class Scanner:
    """认知扫描器 — 三层扫描"""

    def __init__(self, store: CognitionStore):
        self.store = store
        Scanner._lock = Scanner._lock or __import__("threading").Lock()

    # ── 信号层扫描 ──

    def scan_signal(self, message: str) -> list[tuple[CognitionType, str]]:
        """
        信号层：从用户输入的 message 中检测认知触发信号。

        返回：[(CognitionType, trigger_text), ...]
        """
        results = []
        for ctype, pattern in SIGNAL_PATTERNS.items():
            m = pattern.search(message)
            if m:
                results.append((ctype, m.group(0)))
        return results

    # ── 并发保护 ──
    _lock = None

    # ── 概念层扫描 ──

    def scan_concept(self, message: str, agent: str = "") -> list[CognitionSlice]:
        """
        概念层：根据当前消息和 Agent 名，从认知库中匹配同类场景的切片。
        """
        # 提取场景关键词
        scene_keywords = []
        for kw in ["写代码", "审计", "部署", "搜索", "调研", "写作", "决策"]:
            if kw in message:
                scene_keywords.append(kw)

        results = []
        for kw in scene_keywords:
            results.extend(self.store.search_by_concept(scene=kw, agent=agent))

        # 按置信度去重排序
        seen = set()
        unique = []
        for s in sorted(results, key=lambda x: -x.confidence):
            if s.id not in seen:
                seen.add(s.id)
                unique.append(s)
        return unique[:10]

    # ── 策略层扫描 ──

    def scan_strategy(self, message: str, _agent: str = "") -> list[CognitionSlice]:
        """
        策略层：从认知库中按内容片段匹配策略。
        """
        return self.store.search_by_strategy(message[:20])

    # ── 三层联合扫描 ──

    def full_scan(self, message: str, agent: str = "", min_confidence: float = 0.3) -> list[CognitionSlice]:
        """
        三层联合扫描：
        1. 信号层：检测认知类型触发
        2. 概念层：匹配同类场景
        3. 策略层：文本片段匹配
        4. 统一搜索：联合查询

        去重并按置信度排序。
        """
        with Scanner._lock:
            matched = {}
            signals = self.scan_signal(message)
            signal_types = set(ctype.value for ctype, _ in signals)

        # 信号层：直接匹配认知库
        if signal_types:
            for st in signal_types:
                # 从 SIGNAL_PATTERNS 获取该类型对应的关键词/模式
                kw = st  # st 已是字符串如 'DIRECTION_CORRECTION'
                for s in self.store.search_by_signal(keywords=[kw], agent=agent):
                    if s.confidence >= min_confidence:
                        matched[s.id] = s

        # 概念层
        for s in self.scan_concept(message, agent):
            if s.confidence >= min_confidence:
                matched[s.id] = s

        # 策略层
        for s in self.scan_strategy(message, agent):
            if s.confidence >= min_confidence:
                matched[s.id] = s

        # 统一搜索
        for s in self.store.unified_search(message, agent, min_confidence):
            matched[s.id] = s

        # 排序
        result = sorted(matched.values(), key=lambda x: -x.confidence)
        return result[:15]

    # ── 信号提醒辅助 ──

    def get_signal_types(self, message: str) -> list[CognitionType]:
        """仅返回匹配到的认知类型"""
        signals = self.scan_signal(message)
        return [ctype for ctype, _ in signals]

    def should_remind(self, message: str) -> bool:
        """快速判断是否应该触发认知提醒"""
        signals = self.scan_signal(message)
        return len(signals) > 0
