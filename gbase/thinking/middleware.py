"""Lever Middleware — Hooks L0 + L1 + L4 into GBase kernel pipeline."""

import json
import logging
from datetime import datetime

from gbase.thinking.router import classify_task, format_injection
from gbase.thinking.context import context_scan, problem_mapping
from gbase.thinking.reflection import ReflectionLever

logger = logging.getLogger("gbase.thinking")

# ── L0 + L1: 前置思考注入 ──

def enrich_with_thinking(user_message: str) -> tuple[str, dict]:
    """Run L0 context scanning + L1 thinking classification.
    
    Returns (enriched_message, thinking_meta) where thinking_meta
    contains structured context for later pipeline stages.
    """
    # L1: Classify task intent
    classification = classify_task(user_message)
    injection = format_injection(classification)
    
    # L0: Scan context entities, actions, constraints
    context = context_scan(user_message)
    problem = problem_mapping(user_message)
    
    thinking_meta = {
        "classification": classification,
        "context": context,
        "problem": problem,
        "timestamp": datetime.now().isoformat(),
    }
    
    # Build enriched message
    parts = []
    
    if injection:
        parts.append(injection)
    
    entities = context.get("entities", [])
    if entities:
        parts.append(f"[context: {', '.join(entities)}]")
    
    constraints = context.get("constraints", [])
    if constraints:
        parts.append(f"[constraints: {', '.join(constraints)}]")
    
    problem_dims = problem.get("dimensions", {})
    if problem_dims:
        dim_str = " | ".join(f"{k}={v}" for k, v in problem_dims.items())
        parts.append(f"[task_profile: {dim_str}]")
    
    enriched = "\n".join(parts) + "\n\n" + user_message if parts else user_message
    return enriched, thinking_meta


# ── L4: 后置反思 ──

def reflect_on_reply(reply: str) -> dict:
    """L4 reflection: self-check quality, optionally refine."""
    lever = ReflectionLever()
    check = lever.self_check(reply)
    if not check.get("is_satisfied", False):
        refined = lever.refine(reply, check)
        return {"original_check": check, "refined": refined}
    return {"original_check": check, "refined": None}


# ── 便捷入口 ──

def process_pipeline(user_message: str) -> tuple[str, dict, dict | None]:
    """Full L0-L1-L4 pipeline in one call.
    
    Returns (enriched_message, thinking_meta, reflection_result).
    """
    enriched, meta = enrich_with_thinking(user_message)
    return enriched, meta, None  # L4 runs after reply is available
