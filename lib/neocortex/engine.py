#!/usr/bin/env python3
"""
认知新皮质 — 核心引擎
===================
扫描器 + 决策器 + 反馈闭环，合为一个接口。

Agent 只需要调这一个接口：
  engine = CognitionEngine()
  action = engine.process(user_message, agent_name)
  if action.action != "pass":
      # 注入认知提醒到回复中
"""

import contextlib

from .scanner import Scanner
from .schema import CognitionType, FeedbackType
from .store import CognitionStore


class CognitionAction:
    """认知决策结果"""

    def __init__(self, action: str = "pass", priority: str = "LOW", message: str = "", matches: list = None):
        self.action = action  # 'warn' / 'reference' / 'pass'
        self.priority = priority  # 'HIGH' / 'MEDIUM' / 'LOW'
        self.message = message
        self.matches = matches or []

    def __repr__(self):
        return f"[{self.action.upper()}/{self.priority}] {self.message[:60]}"

    def to_agent_prompt(self) -> str:
        """生成嵌入 Agent prompt 的认知提醒文本"""
        if self.action == "pass":
            return ""
        return self.message


class CognitionEngine:
    """
    认知引擎

    使用方式：
        ce = CognitionEngine()
        action = ce.process("帮我查一下GitHub", "橘子")
        if action.action != "pass":
            response = f"{action.to_agent_prompt()}\n\n{response}"
        ce.after_action(action, "对，就这样", "橘子")
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = "/opt/orange-arm-v2/cognition_demo/data/cognition.db"
        self.store = CognitionStore(db_path)
        self.scanner = Scanner(self.store)

    # ── 完整流程 ──

    def process(self, user_message: str, agent_name: str = "", _session_history: list = None) -> CognitionAction:
        """
        scan → decide → 返回决策

        这是 Agent 回复前只需要调这一个接口。
        """
        try:
            matches = self.scanner.full_scan(user_message, agent_name)
        except Exception as e:
            __import__("logging").getLogger(__name__).error(f"Cognition scan failed: {e}")
            return CognitionAction()

        # 更新访问计数（批量）
        if matches:
            self.store.increment_access([m.id for m in matches if m.id])

        return self.decide(matches)

    # ── 决策器 ──

    def decide(self, matches: list) -> CognitionAction:
        """
        基于扫描结果，决定是否干预。

        规则：
        - 方向纠偏且置信度 >= 0.5 → WARN
        - 任意类型且置信度 >= 0.8 → WARN
        - 任意类型且置信度 >= 0.5 → REFERENCE
        - 其他 → PASS
        """
        if not matches:
            return CognitionAction()

        top = matches[0]

        # 判断是否有方向纠偏
        has_correction = any(
            m.cognition_type == CognitionType.DIRECTION_CORRECTION and m.confidence >= 0.5 for m in matches
        )

        if has_correction:
            # 方向纠偏即使置信度稍低也触发提醒
            next(m for m in matches if m.cognition_type == CognitionType.DIRECTION_CORRECTION)
            messages = []
            for m in matches[:3]:
                ctype_label = m.cognition_type.value
                lesson = m.strategy_layer.lesson[:80] if m.strategy_layer else "无内容"
                messages.append(f" [{ctype_label}] {lesson}")

            return CognitionAction(
                action="warn",
                priority="HIGH",
                message="目标纠正信号，历史经验提醒：\n" + "\n".join(messages),
                matches=matches,
            )

        elif top.confidence >= 0.8:
            # 高置信度 → 提醒
            lesson = top.strategy_layer.lesson[:100] if top.strategy_layer else "无内容"
            return CognitionAction(action="warn", priority="HIGH", message=f"历史经验提醒：{lesson}", matches=matches)

        elif top.confidence >= 0.5:
            # 中等置信度 → 参考
            lesson = top.strategy_layer.lesson[:100] if top.strategy_layer else "无内容"
            return CognitionAction(
                action="reference", priority="MEDIUM", message=f"相关历史参考：{lesson}", matches=matches
            )

        else:
            return CognitionAction()

    # ── 反馈闭环 ──

    def after_action(self, action: CognitionAction, user_feedback_raw: str = "", agent_name: str = ""):
        """
        Post-action: record user feedback on cognitive reminders.

        If a matching slice exists, infer feedback type from user's response and adjust confidence.
        """
        if not action.matches or not user_feedback_raw:
            return

        # 推断反馈类型
        ftype = None
        with contextlib.suppress(Exception):
            ftype = self._infer_feedback_type(user_feedback_raw)
        if not ftype:
            return

        for m in action.matches:
            if not m.id:
                continue
            try:
                self.store.record_feedback(m.id, ftype, agent_name, user_feedback_raw)
            except Exception as e:
                __import__("logging").getLogger(__name__).error(f"Cognition feedback failed: {e}")

    def _infer_feedback_type(self, text: str) -> FeedbackType | None:
        """Infer feedback type from user's original response"""
        text_lower = text.lower()

        if (
            any(kw in text_lower for kw in ["好的", "可以", "没错", "是的", "就这样", "挺好", "采纳"])
            or text_lower.startswith("对")
            or text_lower == "对"
        ):
            return FeedbackType.ADOPTED
        elif any(kw in text_lower for kw in ["不用", "不需要", "不必要", "不必", "过"]):
            return FeedbackType.IGNORED
        elif any(kw in text_lower for kw in ["不对", "错了", "不是这样", "理解错", "不是这个"]):
            return FeedbackType.CORRECTED

        return None

    def shutdown(self):
        """关闭引擎，释放数据库连接"""
        with contextlib.suppress(Exception):
            self.store.close()

    # ── 种子认知 ──

    def seed_default(self, logs: list = None):
        """
        从已有数据长种子认知。

        如果提供了学徒日志 raw dict，从中提取种子。
        否则用蒸馏器从已有日志提取。
        """
        if logs:
            from .distiller import NeocortexDistiller

            distiller = NeocortexDistiller(
                apprentice_dir="/opt/orange-arm-v2/apprentice/logs/", db_path=self.store.db_path
            )
            return distiller.distill()
        return {"slices_created": 0, "total_slices": 0}
