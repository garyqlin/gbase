"""
L4 ReflectionLever — Self-Check & Iterative Refinement

Gives any LLM-powered system the ability to self-check and iteratively
improve its own output through structured feedback loops.

This is a pure-rule base implementation. For production use, wrap it
with a model call for deeper reflection.
"""

from collections.abc import Callable


class ReflectionLever:
    """Self-reflection lever — enables iterative output refinement.

    Args:
        model_call: Optional callable with signature (prompt: str) -> str.
                    If provided, used for LLM-based reflection/rewriting.
                    If None, only rule-based checks apply.
    """

    def __init__(self, model_call: Callable | None = None):
        self.model_call = model_call
        self.max_rounds = 3

    def refine(
        self,
        draft: str,
        criteria: list[str] | None = None,
        max_rounds: int = 3,
    ) -> dict:
        """Iteratively refine a draft answer.

        Args:
            draft: Initial answer text.
            criteria: Evaluation criteria list
                      (e.g. ["clarity", "accuracy", "completeness"]).
            max_rounds: Maximum refinement iterations.

        Returns:
            {"final_answer": str, "rounds": int, "history": [...]}
        """
        if criteria is None:
            criteria = ["clarity", "accuracy", "completeness"]

        history = [{"round": 0, "answer": draft, "feedback": None}]
        current_answer = draft

        for round_num in range(1, max_rounds + 1):
            feedback = self._reflect(current_answer, criteria)

            if feedback.get("is_satisfied", False):
                break

            revised = self._revise(current_answer, feedback)

            history.append(
                {
                    "round": round_num,
                    "answer": revised,
                    "feedback": feedback.get("comments", ""),
                }
            )
            current_answer = revised

        return {
            "final_answer": current_answer,
            "rounds": len(history) - 1,
            "history": history,
        }

    def _reflect(self, answer: str, criteria: list[str]) -> dict:
        """Self-check: evaluate answer quality against criteria.

        Returns:
            {"is_satisfied": bool, "comments": str, "issues": [...]}
        """
        issues = []

        if len(answer) < 50:
            issues.append("Answer too short, may lack detail")

        if not any(
            kw in answer
            for kw in [
                "first",
                "second",
                "finally",
                "1.",
                "2.",
                "3.",
                "首先",
                "其次",
                "然后",
                "最后",
            ]
        ):
            issues.append("Lacks structured presentation")

        for criterion in criteria:
            keyword_map = {
                "clarity": ["明确", "清晰", "具体", "specifically", "clearly"],
                "accuracy": ["准确", "精确", "精确", "correct", "accurate"],
                "completeness": ["所有", "全部", "覆盖", "all", "complete", "coverage"],
                "简洁": ["简洁", "精简", "精简", "concise"],
                "逻辑": ["逻辑", "合理", "原因", "because", "therefore"],
            }
            for eng_criterion, keywords in keyword_map.items():
                if criterion.lower() in eng_criterion or criterion in keywords:
                    if not any(kw in answer for kw in keywords):
                        issues.append(f"Could better address '{criterion}'")
                    break

        is_satisfied = len(issues) == 0

        return {
            "is_satisfied": is_satisfied,
            "comments": "; ".join(issues) if issues else "Quality looks good",
            "issues": issues,
        }

    def _revise(self, answer: str, feedback: dict) -> str:
        """Revise based on feedback. Uses model_call if available."""
        if self.model_call:
            prompt = (
                f"Revise the following answer to address these issues: "
                f"{feedback.get('comments', '')}\n\nAnswer:\n{answer}"
            )
            return self.model_call(prompt)

        issues = feedback.get("issues", [])
        revised = answer

        if "Lacks structured presentation" in issues:
            revised = (
                f"Summary: {revised}\n\n"
                f"Key details:\n"
                f"1. {revised}\n"
                f"2. Additional context.\n"
                f"Conclusion: summarized above."
            )

        if "Answer too short" in issues:
            revised = f"{revised}\n\nExpanded: additional details and analysis."

        return revised

    def self_check(
        self,
        answer: str,
        criteria: list[str] | None = None,
    ) -> dict:
        """Single-pass self-check (no iteration).

        Args:
            answer: Answer text to evaluate.
            criteria: Evaluation criteria.

        Returns:
            {"is_satisfied": bool, "comments": str, "issues": [...]}
        """
        if criteria is None:
            criteria = ["clarity", "accuracy", "completeness"]

        return self._reflect(answer, criteria)
