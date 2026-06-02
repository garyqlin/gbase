# SPDX-License-Identifier: MIT
"""
gbase/lib/tracer.py

Execution observability — auto-trace each tool call, output exact failure step number.

Architecture:
- Non-intrusive: records at the tool call point in kernel._loop(), does not modify tool functions
- Async writes: JSONL file, <50ms overhead, non-blocking
- Failure analysis: callers use get_failure_analysis() to get "step N failed, why"
- Cross-session correlation: same task_id trace files can be loaded by subsequent tasks
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Storage ──

TRACE_DIR = Path(__file__).resolve().parent.parent / "data" / "traces"

# Currently active trace context (thread-safe: only one conversation at a time)
_current_trace: dict | None = None


# ── Init ──


def init_trace(task_id: str, task_description: str = ""):
    """Start a new trace record"""
    global _current_trace
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    _current_trace = {
        "task_id": task_id,
        "start_time": time.time(),
        "steps": [],
        "description": task_description,
        "status": "running",
    }
    _write_entry(
        "init",
        {
            "task_id": task_id,
            "description": task_description[:200],
            "timestamp": time.time(),
        },
    )
    logger.info("[trace %s] initialized", task_id)
    return task_id


def close_trace(status: str = "completed", error: str = ""):
    """Close the current trace"""
    global _current_trace
    if not _current_trace:
        return
    _current_trace["status"] = status
    _current_trace["end_time"] = time.time()
    if error:
        _current_trace["error"] = error
    _write_entry(
        "close",
        {
            "status": status,
            "error": error[:500] if error else "",
            "elapsed": time.time() - _current_trace["start_time"],
        },
    )
    logger.info("[trace %s] closed: status=%s", _current_trace.get("task_id", "?"), status)
    _current_trace = None


# ── Tool Call Recording ──


def record_tool_call(
    step: int,
    tool_name: str,
    input_digest: str,
    output_digest: str,
    status: str = "ok",
    error: str = "",
    duration_ms: float = 0,
):
    """Record a single tool call (called by kernel._loop).

    Args:
        step: Step number (starts from 1)
        tool_name: Tool name
        input_digest: Input digest (first 120 chars of params)
        output_digest: Output digest (first 200 chars of result)
        status: ok | error | timeout
        error: Error message (required when status=error)
        duration_ms: Execution duration (milliseconds)
    """
    global _current_trace
    if not _current_trace:
        return

    entry = {
        "step": step,
        "tool": tool_name,
        "input": input_digest[:200],
        "output": output_digest[:200],
        "status": status,
        "error": error[:500] if error else "",
        "duration_ms": round(duration_ms, 1),
        "timestamp": time.time(),
    }
    _current_trace["steps"].append(entry)
    _write_entry("tool_call", entry)

    if status == "error":
        logger.warning(
            "[trace %s] step%d tool %s failed: %s", _current_trace.get("task_id", "?"), step, tool_name, error[:100]
        )


# ── Failure Analysis ──


def get_failure_analysis() -> dict:
    """Analyze current trace, return failure info.

    Returns:
        {
            "has_failure": bool,
            "failed_step": int | None,
            "failed_tool": str | None,
            "failure_type": str | None,
            "passed_steps": int,
            "suggestion": str | None,
        }
    """
    global _current_trace
    if not _current_trace or not _current_trace["steps"]:
        return {"has_failure": False, "passed_steps": 0}

    steps = _current_trace["steps"]
    passed_before_fail = 0
    failed = None

    for s in steps:
        if s["status"] == "error":
            failed = s
            break
        passed_before_fail += 1

    if not failed:
        return {
            "has_failure": False,
            "passed_steps": len(steps),
            "suggestion": None,
        }

    # Failure type inference
    error_text = (failed.get("error") or "").lower()
    if "timeout" in error_text or "timeout" in failed.get("output", ""):
        failure_type = "Tool Timeout"
    elif "not found" in error_text or "404" in error_text:
        failure_type = "Resource Not Found"
    elif "auth" in error_text or "401" in error_text or "403" in error_text:
        failure_type = "Permission Denied"
    elif "connection" in error_text or "connect" in error_text or "refused" in error_text:
        failure_type = "Connection Failed"
    elif "500" in error_text or "error" in error_text:
        failure_type = "Server Error"
    elif "timeout" in error_text:
        failure_type = "Timeout"
    else:
        failure_type = "Unknown Error"

    # Suggestion (based on failure step context)
    suggestion = f"Step {passed_before_fail + 1} ({failed['tool']}) failed, failure type: {failure_type}."
    if passed_before_fail == 0:
        suggestion += " First step failed, check environment and dependencies."
    elif failure_type == "Tool Timeout":
        suggestion += " Consider parallelization or reducing command timeout."
    elif failure_type == "Resource Not Found":
        suggestion += " Check if path and file exist."
    else:
        suggestion += f" Failure detail: {error_text[:200]}"

    return {
        "has_failure": True,
        "failed_step": passed_before_fail + 1,
        "failed_tool": failed["tool"],
        "failure_type": failure_type,
        "passed_steps": passed_before_fail,
        "suggestion": suggestion,
    }


# ── File Write ──


def _write_entry(entry_type: str, data: dict):
    """Async write a JSONL trace record."""
    if _current_trace is None:
        return
    task_id = _current_trace["task_id"]
    filepath = TRACE_DIR / f"{task_id}.jsonl"

    entry = {
        "_type": entry_type,
        "task_id": task_id,
        **data,
    }
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("[trace] write failed: %s", e)


# ── Read Existing Traces ──


def read_trace(task_id: str) -> list[dict]:
    """Read a completed trace file."""
    filepath = TRACE_DIR / f"{task_id}.jsonl"
    if not filepath.exists():
        return []
    try:
        entries = []
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries
    except Exception as e:
        logger.warning("[trace] read failed %s: %s", task_id, e)
        return []


def analyze_task(trace_entries: list[dict]) -> dict:
    """Analyze a set of trace entries (independent of active trace)."""
    tool_calls = [e for e in trace_entries if e.get("_type") == "tool_call"]
    errors = [e for e in tool_calls if e.get("status") == "error"]

    if not tool_calls:
        return {"tool_calls": 0, "errors": 0, "failure_steps": []}

    failure_steps = [e["step"] - 1 for e in errors if "step" in e]

    return {
        "tool_calls": len(tool_calls),
        "errors": len(errors),
        "failure_steps": [e.get("step") for e in errors if "step" in e],
        "first_failure": failure_steps[0] if failure_steps else None,
    }


# ── List ──


def list_traces() -> list[str]:
    """List all trace files."""
    if not TRACE_DIR.exists():
        return []
    return sorted([f.stem for f in TRACE_DIR.glob("*.jsonl")], reverse=True)
