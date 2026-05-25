# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/tracer.py

Execution observability — auto-trace each tool call, output exact failure step number.

架构：
- 无侵入：通过包装 kernel._loop() 中的工具调用点记录，不改工具函数本身
- 异步写入：JSONL 文件，<50ms 开销，不阻塞主流程
- 失败分析：调用方可通过 get_failure_analysis() 拿到"第N步失败，失败原因"
- 跨session关联：同一个 task_id 的 trace 文件可被后续任务读入
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 存储 ──

TRACE_DIR = Path(__file__).resolve().parent.parent / "data" / "traces"

# 当前活跃的 trace 上下文（线程安全：同一时间只有一个对话）
_current_trace: dict | None = None


# ── 初始化 ──

def init_trace(task_id: str, task_description: str = ""):
    """开始一个新的 trace 追踪记录"""
    global _current_trace
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    _current_trace = {
        "task_id": task_id,
        "start_time": time.time(),
        "steps": [],
        "description": task_description,
        "status": "running",
    }
    _write_entry("init", {
        "task_id": task_id,
        "description": task_description[:200],
        "timestamp": time.time(),
    })
    logger.info("[trace %s] 初始化", task_id)
    return task_id


def close_trace(status: str = "completed", error: str = ""):
    """关闭当前 trace"""
    global _current_trace
    if not _current_trace:
        return
    _current_trace["status"] = status
    _current_trace["end_time"] = time.time()
    if error:
        _current_trace["error"] = error
    _write_entry("close", {
        "status": status,
        "error": error[:500] if error else "",
        "elapsed": time.time() - _current_trace["start_time"],
    })
    logger.info("[trace %s] 关闭: status=%s", _current_trace.get("task_id", "?"), status)
    _current_trace = None


# ── 工具调用记录 ──

def record_tool_call(
    step: int,
    tool_name: str,
    input_digest: str,
    output_digest: str,
    status: str = "ok",
    error: str = "",
    duration_ms: float = 0,
):
    """记录一次工具调用（由 kernel._loop 调用）。

    Args:
        step: 步骤号（从1开始递增）
        tool_name: 工具名称
        input_digest: 输入摘要（参数的前120字）
        output_digest: 输出摘要（结果的前200字）
        status: ok | error | timeout
        error: 错误信息（status=error时必填）
        duration_ms: 执行耗时（毫秒）
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
        logger.warning("[trace %s] 步骤%d 工具 %s 失败: %s",
                       _current_trace.get("task_id", "?"), step, tool_name, error[:100])


# ── 失败分析 ──

def get_failure_analysis() -> dict:
    """分析当前 trace，返回失败信息。

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

    # 失败类型推断
    error_text = (failed.get("error") or "").lower()
    if "timeout" in error_text or "timeout" in failed.get("output", ""):
        failure_type = "工具超时"
    elif "not found" in error_text or "404" in error_text:
        failure_type = "资源不存在"
    elif "auth" in error_text or "401" in error_text or "403" in error_text:
        failure_type = "权限不足"
    elif "connection" in error_text or "connect" in error_text or "refused" in error_text:
        failure_type = "连接失败"
    elif "500" in error_text or "error" in error_text:
        failure_type = "服务端错误"
    elif "timeout" in error_text:
        failure_type = "超时"
    else:
        failure_type = "未知错误"

    # 建议（基于失败步骤前后文）
    suggestion = f"第{passed_before_fail + 1}步({failed['tool']})失败，失败类型：{failure_type}。"
    if passed_before_fail == 0:
        suggestion += "第一步即失败，检查环境和依赖是否就绪。"
    elif failure_type == "工具超时":
        suggestion += "考虑并行化或缩短命令超时。"
    elif failure_type == "资源不存在":
        suggestion += "检查路径和文件是否存在。"
    else:
        suggestion += f"失败详情：{error_text[:200]}"

    return {
        "has_failure": True,
        "failed_step": passed_before_fail + 1,
        "failed_tool": failed["tool"],
        "failure_type": failure_type,
        "passed_steps": passed_before_fail,
        "suggestion": suggestion,
    }


# ── 文件写入 ──

def _write_entry(entry_type: str, data: dict):
    """异步写入一条 JSONL trace 记录。"""
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
        logger.warning("[trace] 写入失败: %s", e)


# ── 读取已有 trace ──

def read_trace(task_id: str) -> list[dict]:
    """读取已完成的 trace 文件。"""
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
        logger.warning("[trace] 读取失败 %s: %s", task_id, e)
        return []


def analyze_task(trace_entries: list[dict]) -> dict:
    """对一组 trace 条目做分析（独立于 active trace）。"""
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


# ── 列表 ──

def list_traces() -> list[str]:
    """列出所有 trace 文件。"""
    if not TRACE_DIR.exists():
        return []
    return sorted([f.stem for f in TRACE_DIR.glob("*.jsonl")], reverse=True)
