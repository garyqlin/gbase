"""thinking — Structured thinking levers for GBase.

Provides L0 (Context), L1 (Thinking Router), L3 (Verification), 
L4 (Reflection) levers that hook into the GBase kernel pipeline.
"""
from gbase.thinking.router import classify_task, format_injection
from gbase.thinking.context import context_scan, problem_mapping
from gbase.thinking.reflection import ReflectionLever
from gbase.thinking.execution import verify_code, verify_config, verify_fact
