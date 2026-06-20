"""thinking — Structured thinking levers for GBase.

Provides L0 (Context), L1 (Thinking Router), L3 (Verification),
L4 (Reflection) levers that hook into the GBase kernel pipeline.
"""

__all__ = [
    "classify_task",
    "format_injection",
    "context_scan",
    "problem_mapping",
    "verify_result",
    "ReflectionLever",
]

from gbase.thinking.context import context_scan, problem_mapping
from gbase.thinking.execution import verify_result
from gbase.thinking.reflection import ReflectionLever
from gbase.thinking.router import classify_task, format_injection
