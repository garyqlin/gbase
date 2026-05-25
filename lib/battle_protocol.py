# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/battle_protocol.py

Agent communication protocol - send tasks to agents, auto-return results.

Protocol format:
    {
        "task_id": "unique ID",
        "type": "audit_code | evaluate_api | explore_project | build_frontend | review_design",
        "target": "project name/path",
        "scope": "specific scope (optional, default: full)",
        "callback_url": "POST results to this URL on completion (optional)",
        "context": "additional context (optional)"
    }

Routes (registered in main.py):
    POST /hammer/audit   → send code review
    POST /ink/evaluate   → send quality evaluation

Callback format (POST callback_url):
    {
        "task_id": "same ID",
        "type": "completed task type",
        "status": "completed | failed",
        "result": "task output text",
        "trace_id": "associated trace ID",
        "error": "failure reason (if any)",
        "elapsed_seconds": elapsed time
    }
"""

import logging

logger = logging.getLogger(__name__)

# ── Task types ──

TASK_TYPES = {
    "audit_code": "code review (agent-1)",
    "evaluate_api": "API quality eval (agent-2)",
    "explore_project": "project exploration/info gathering",
    "build_frontend": "frontend dev (agent-2)",
    "review_design": "design review (agent-2)",
}

# ── Build task message ──

def build_task_message(task: dict) -> str:
    """Convert protocol tasks to natural language instructions for agents."""
    task_type = task.get("type", "unknown")
    target = task.get("target", "")
    scope = task.get("scope", "")
    context = task.get("context", "")

    type_prompts = {
        "audit_code": (
            f"[Task: Code Review]\n"
            f"Please perform a full white-box code review on project '{target}'.\n"
            f"Scope: {scope or 'full'}.\n"
            f"Three steps: read project structure → line-by-line review → write report.\n"
            f"Focus: security issues, performance risks, code redundancy, error handling."
        ),
        "evaluate_api": (
            f"[Task: API Quality Assessment]\n"
            f"Please perform a black-box API test on project '{target}'.\n"
            f"Scope: {scope or 'all endpoints'}.\n"
            f"Three steps: verify project/service is running → batch test → produce assessment report.\n"
            f"Mark each result (verified) or (inferred, not verified)."
        ),
        "explore_project": (
            f"[Task: Project Exploration]\n"
            f"Please explore the structure and state of project '{target}'.\n"
            f"Scope: {scope or 'full'}.\n"
            f"Output: project path, top 10 file structure, service status, framework/db version."
        ),
        "build_frontend": (
            f"[Task: Frontend Development]\n"
            f"Please develop frontend page: {target}\n"
            f"Scope: {scope or 'full'}.\n"
            f"First determine layout → write HTML/CSS/JS → screenshot verify."
        ),
        "review_design": (
            f"[Task: Design Review]\n"
            f"Please review design: {target}\n"
            f"Scope: {scope or 'full'}.\n"
            f"Focus: color consistency, responsive adaptation, animation smoothness."
        ),
    }

    msg = type_prompts.get(
        task_type,
        f"[Task: {task_type}]\nPlease process project '{target}', scope: {scope or 'full'}."
    )

    if context:
        msg += f"\n\nAdditional context:\n{context}"

    # stability reminder
    msg += "\n\nThree steps: 1) read project 2) execute task 3) produce report. Write JSON intermediates per step."

    return msg


# ── Callback ──

async def send_callback(callback_url: str, payload: dict):
    """Async send callback result. Failure does not affect main flow."""
    if not callback_url:
        return
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session, session.post(
            callback_url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                logger.warning("[protocol] callback returned %s: %s", resp.status, await resp.text()[:100])
            else:
                logger.info("[protocol] callback success: %s", callback_url[:80])
    except TimeoutError:
        logger.warning("[protocol] callback timeout: %s", callback_url[:80])
    except Exception as e:
        logger.warning("[protocol] callback error: %s", e)


def make_callback_payload(task_id: str, task_type: str, status: str,
                          result: str, trace_id: str = "", error: str = "") -> dict:
    """Build callback payload."""
    return {
        "task_id": task_id,
        "type": task_type,
        "status": status,
        "result": result[:8000],
        "trace_id": trace_id,
        "error": error[:500] if error else "",
        "elapsed_seconds": 0,
    }


# ── Type validation ──

def validate_task(task: dict) -> str | None:
    """Validate task structure. Returns None for pass, string for error."""
    if "type" not in task:
        return "missing field: type"
    if task["type"] not in TASK_TYPES:
        return f"unknown task type: {task['type']}, available: {', '.join(TASK_TYPES.keys())}"
    if "target" not in task or not task["target"]:
        return "missing field: target"
    return None
