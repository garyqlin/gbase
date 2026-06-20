"""
L1 ThinkingLever — Intent Signature Router

A lightweight (<5ms, pure regex) task pattern classifier.
Given a user message, it detects the type of reasoning needed
and recommends a structured thinking method.

Usage:
    from thinking_lever.core.router import classify_task, format_injection
    result = classify_task("Why is my API returning 500?")
    inject = format_injection(result)
"""

from typing import Dict, List, Optional, Any
import re

# ──────────────────────────────────────────────
# Task patterns (5 modes)
# ──────────────────────────────────────────────

PATTERN_DIAGNOSE = "diagnose"       # Symptoms → find root cause
PATTERN_DESIGN = "design"           # Requirements → produce plan
PATTERN_OPTIMIZE = "optimize"       # Current state → find improvements
PATTERN_PREDICT = "predict"         # Trends → forecast outcomes
PATTERN_EXECUTE = "execute"         # Clear steps → SKIP deep thinking

# ──────────────────────────────────────────────
# Thinking methods
# ──────────────────────────────────────────────

THINKING_METHODS = {
    "first_principles": "对基本事实和原理的质疑与重建",
    "reverse_inference": "从期望结果反向推导路径",
    "mece_decomposition": "相互独立、完全穷尽的分解",
    "second_order": "超越直接效应，思考连锁影响",
    "constraint_analysis": "识别并优化关键约束",
    "root_cause": "通过排除法找到根本原因",
    "counterfactual": "通过反事实假设进行推演",
    "extreme_test": "极端值和边界条件测试",
}

# ──────────────────────────────────────────────
# Pattern matching rules
# ──────────────────────────────────────────────

QUESTION_PATTERNS: List[tuple] = [
    # Diagnostic
    (r"为什么|原因|根因|哪里出错|故障|报错|错误|cause|root cause|why did|what broke", PATTERN_DIAGNOSE),
    # Predictive (higher priority than design)
    (r"预测|趋势|将来的|未来|impact|predict|trend|forecast|影响|走向|上线后|长期", PATTERN_PREDICT),
    # Design
    (r"设计|方案|规划|架构|搭建|实现|怎么做|如何|how to|architecture|design|build", PATTERN_DESIGN),
    # Optimization
    (r"优化|改善|改进|性能|瓶颈|更快|更好|optimize|improve|bottleneck|faster|better", PATTERN_OPTIMIZE),
]

VERB_TO_METHOD: Dict[str, str] = {
    # Design → first principles
    "从零": "first_principles",
    "重新设计": "first_principles",
    "核心本质": "first_principles",
    "去掉": "first_principles",
    # Reverse inference
    "反推": "reverse_inference",
    "逆推": "reverse_inference",
    "从结果": "reverse_inference",
    # MECE decomposition
    "分解": "mece_decomposition",
    "分类": "mece_decomposition",
    "归纳": "mece_decomposition",
    "梳理": "mece_decomposition",
    # Second-order thinking
    "连锁": "second_order",
    "副作用": "second_order",
    "间接影响": "second_order",
    # Root cause
    "根本原因": "root_cause",
    "溯源": "root_cause",
    "挖根": "root_cause",
    # Extreme / boundary
    "对比": "extreme_test",
    "比较": "extreme_test",
    "极端": "extreme_test",
    "边界": "extreme_test",
    "极限": "extreme_test",
}

DOMAIN_SIGNALS: List[tuple] = [
    (r"bug|crash|崩溃|挂掉|不工作|corrupt|missing|error|异常", "debug", PATTERN_DIAGNOSE),
    (r"feature|function|新功能|添加|加上|增加", "feature", PATTERN_DESIGN),
    (r"refactor|重构|重写|清理|clean|整理|改结构", "refactor", PATTERN_DESIGN),
    (r"test|测试|验证|assert|spec", "test", PATTERN_EXECUTE),
    (r"config|配置|环境|环境变量|env|setup|安装", "config", PATTERN_EXECUTE),
    (r"调研|research|搜索|搜|查询|search", "research", PATTERN_DIAGNOSE),
]

SKIP_PATTERNS: List[str] = [
    r"^好的|^明白|^收到|^是|^嗯|^OK|^ok|^got it",
    r"^你好|^hi|^hello",
    r"^几点|^时间|^日期|^今天|^weather",
    r"^谢谢|^谢|^thanks|^thank you",
    r"^继续|^继续做|^接着|^go on|^continue",
    r"^干$|^开工|^start|^开始",
]


def _should_skip(message: str) -> bool:
    """Check if L1 should be skipped (simple messages, greetings)."""
    msg = message.strip().lower()
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, msg):
            return True
    if len(msg) < 5:
        return True
    method_signals = ["用", "使用", "调用", "运行", "跑", "执行", "派"]
    if any(msg.startswith(s) for s in method_signals) and len(msg) < 20:
        return True
    return False


def _match_by_question_type(message: str) -> Optional[str]:
    for pattern, method in QUESTION_PATTERNS:
        if re.search(pattern, message):
            return method
    return None


def _match_by_verb_signal(message: str) -> Optional[str]:
    for signal, method in VERB_TO_METHOD.items():
        if signal in message:
            return method
    return None


def _match_by_domain(message: str) -> Optional[str]:
    for pattern, domain, method in DOMAIN_SIGNALS:
        if re.search(pattern, message, re.IGNORECASE):
            return method
    return None


def _resolve_method(pattern: str, message: str) -> str:
    method = _match_by_verb_signal(message)
    if method:
        return method
    method_map = {
        PATTERN_DIAGNOSE: "root_cause",
        PATTERN_DESIGN: "first_principles",
        PATTERN_OPTIMIZE: "constraint_analysis",
        PATTERN_PREDICT: "second_order",
        PATTERN_EXECUTE: "execute",
    }
    return method_map.get(pattern, "execute")


def classify_task(
    message: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Classify a task into a thinking pattern and recommended method.

    Args:
        message: User message / task description.
        context: Optional conversation context (not yet used, reserved).

    Returns:
        {
            "pattern": str | None,    # Task pattern name
            "method": str | None,     # Thinking method key
            "method_cn": str,         # Chinese description
            "skip": bool,             # Should L1 be skipped?
            "skip_reason": str,       # Why skipped
            "confidence": float,      # Match confidence 0.0-1.0
        }
    """
    result = {
        "pattern": None,
        "method": None,
        "method_cn": "",
        "skip": False,
        "skip_reason": "",
        "confidence": 0.0,
    }

    if _should_skip(message):
        result["skip"] = True
        result["skip_reason"] = "Simple reply or short message"
        return result

    pattern = _match_by_question_type(message)
    if pattern:
        result["pattern"] = pattern
        result["method"] = _resolve_method(pattern, message)
        result["confidence"] = 0.8
        result["method_cn"] = THINKING_METHODS.get(result["method"], "")
        return result

    method = _match_by_verb_signal(message)
    if method:
        result["pattern"] = PATTERN_DESIGN
        result["method"] = method
        result["confidence"] = 0.7
        result["method_cn"] = THINKING_METHODS.get(method, "")
        return result

    pattern = _match_by_domain(message)
    if pattern:
        result["pattern"] = pattern
        result["method"] = _resolve_method(pattern, message)
        result["confidence"] = 0.6
        result["method_cn"] = THINKING_METHODS.get(result["method"], "")
        return result

    if len(message) > 200:
        result["pattern"] = PATTERN_DESIGN
        result["method"] = "mece_decomposition"
        result["confidence"] = 0.4
        result["method_cn"] = THINKING_METHODS.get(result["method"], "")
        return result

    result["skip"] = True
    result["skip_reason"] = "Cannot determine task pattern"
    return result


def format_injection(result: Dict[str, Any]) -> str:
    """Format the classification result for prompt injection."""
    if result.get("skip"):
        return ""

    method_cn = result.get("method_cn", "")
    method = result.get("method", "")
    pattern = result.get("pattern", "")
    confidence = result.get("confidence", 0.0)

    if not method or confidence < 0.5:
        return ""

    return (
        f"\n[thinking_method: {method}]"
        f"\n  └ pattern: {pattern} | method: {method_cn} | confidence: {confidence:.1f}"
    )
