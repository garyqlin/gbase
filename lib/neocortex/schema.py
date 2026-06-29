#!/usr/bin/env python3
"""
认知新皮质 — 数据模型
====================
三层结构：信号层 → 概念层 → 策略层
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class CognitionType(Enum):
    DIRECTION_CORRECTION = "DIRECTION_CORRECTION"
    STRATEGY_CONFIRMATION = "STRATEGY_CONFIRMATION"
    PRIORITY_RULING = "PRIORITY_RULING"
    STANDARD_JUDGMENT = "STANDARD_JUDGMENT"
    DECISION_DELEGATION = "DECISION_DELEGATION"
    OUTPUT_ADJUSTMENT = "OUTPUT_ADJUSTMENT"
    TASK_PATTERN = "TASK_PATTERN"
    SELF_REFLECTION = "SELF_REFLECTION"


class FeedbackType(Enum):
    ADOPTED = "ADOPTED"
    IGNORED = "IGNORED"
    CORRECTED = "CORRECTED"


class SentimentLevel(Enum):
    MILD = "MILD"  # 轻微纠正
    MODERATE = "MODERATE"  # 一般纠正
    SEVERE = "SEVERE"  # 强烈纠正


@dataclass
class LayerSignal:
    """信号层：从原始对话中提取的关键词与匹配模式"""

    keywords: list[str]
    pattern: str  # 正则表达式


@dataclass
class LayerConcept:
    """概念层：信号归纳到的场景、Agent、任务类型"""

    scene: str
    agent: str
    task_type: str


@dataclass
class LayerStrategy:
    """策略层：可复用的经验，及适用 Agent 列表"""

    lesson: str
    applicable_agents: list[str]


@dataclass
class CognitionSlice:
    """认知切片 — 认知新皮质核心单元"""

    id: int = 0
    cognition_type: CognitionType = CognitionType.SELF_REFLECTION
    signal_layer: LayerSignal | None = None
    concept_layer: LayerConcept | None = None
    strategy_layer: LayerStrategy | None = None
    confidence: float = 0.5
    access_count: int = 0
    created_at: str = ""
    last_feedback: str = ""
    source_log: str = ""


@dataclass
class ApprenticeLogEntry:
    """
    学徒日志条目 — 匹配现有 JSONL 格式

    与现有 apprentice-juzi.jsonl 的 schema 对应:
    schema/meta/scene/input/thinking/action/output/reflection
    加上新增的 user_feedback_raw/user_feedback_type/user_sentiment/scene_context
    """

    schema: str = "apprentice-log-v1"
    meta: dict[str, Any] = field(default_factory=dict)
    scene: dict[str, Any] = field(default_factory=dict)
    input_data: dict[str, Any] = field(default_factory=dict)
    thinking: dict[str, Any] = field(default_factory=dict)
    action: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    reflection: dict[str, Any] = field(default_factory=dict)
    # New field — record raw user feedback
    user_feedback_raw: str = ""
    user_feedback_type: str = ""
    user_sentiment: str = ""
    scene_context: str = ""


@dataclass
class ApprenticeConfig:
    """学徒配置"""

    observer_id: str = "橘"
    agent_name: str = "橘子"
    logs_dir: str = "/opt/orange-arm-v2/apprentice/logs/"


def now_iso() -> str:
    """当前时间 ISO 格式"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


# 认知类型 => 中文标签映射
COGNITION_LABELS = {
    CognitionType.DIRECTION_CORRECTION: "方向纠偏",
    CognitionType.STRATEGY_CONFIRMATION: "策略确认",
    CognitionType.PRIORITY_RULING: "优先级裁定",
    CognitionType.STANDARD_JUDGMENT: "标准判定",
    CognitionType.DECISION_DELEGATION: "决策授权",
    CognitionType.OUTPUT_ADJUSTMENT: "输出调整",
    CognitionType.TASK_PATTERN: "任务模式",
    CognitionType.SELF_REFLECTION: "自我反思",
}

# 反馈类型 => 置信度 delta
FEEDBACK_DELTA = {
    FeedbackType.ADOPTED: 0.10,
    FeedbackType.IGNORED: -0.05,
    FeedbackType.CORRECTED: -0.30,
}
