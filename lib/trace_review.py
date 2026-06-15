"""
trace_review.py — 行为复盘分析器（给 Agent 自己用）

不是约束，是行车记录仪。
Agent 每次任务结束后主动调用 review()，从 trace 中提炼三层洞察：

1. 🔍 决策链复盘（trace timeline → 转折点识别）
2. ⚡ 模式提取（重复错误 → 模式签名 → 经验沉淀）
3. 📈 行动建议（当前 trace vs best_skill → 下次改进方向）

设计原则：
- 零侵入 kernel._loop，不拦截不约束
- 纯分析，只读 trace JSONL，写 experience + knowledge
- Agent 自由决定什么时候调 review（可每次任务后自动，也可主动）
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 路径 ──
TRACE_DIR = Path(__file__).resolve().parent.parent / "data" / "traces"
BEST_SKILL_PATH = Path(__file__).resolve().parent.parent / "data" / "best_skill.md"
REVIEW_DIR = Path(__file__).resolve().parent.parent / "data" / "reviews"

# ── 模式签名阈值 ──
_ERROR_SIGNATURE_MERGE_WINDOW = 5  # 同一错误模式在连续5步内出现算一次聚集
_MIN_PATTERN_OCCURRENCES = 2  # 同一模式出现至少2次才提取
_MIN_REVIEW_CONFIDENCE = 0.6  # 建议经验的最低置信度


# ═══════════════════════════════════════════════════════════
#  1. 决策链复盘 — 从 trace JSONL 恢复时间线
# ═══════════════════════════════════════════════════════════


def read_trace(trace_id: str) -> list[dict[str, Any]]:
    """读取一个 trace JSONL 文件。"""
    filepath = TRACE_DIR / f"{trace_id}.jsonl"
    if not filepath.exists():
        return []
    entries = []
    try:
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception as e:
        logger.warning("trace_review: 读取失败 %s: %s", trace_id, e)
    return entries


def _classify_tool_call(tool_name: str, input_digest: str) -> str:
    """给工具调用打上类别标签，方便分析模式。"""
    tn = tool_name.lower()
    inp = input_digest.lower()

    # 文件操作
    if tn in ("read_file", "self_edit_read_source") or "read_file" in tn:
        return "file_read"
    if tn in ("write_file", "self_edit") or "write" in tn:
        return "file_write"
    if tn in ("exec_command", "exec_safe") or "exec" in tn:
        # 区分 shell 命令的内容
        if "node" in inp or "npx" in inp or "npm" in inp or "pnpm" in inp:
            return "shell_node"
        if "find " in inp or "which " in inp or "ls " in inp or "brew " in inp:
            return "shell_path_search"
        if "python3" in inp or "pip" in inp:
            return "shell_python"
        if "git" in inp:
            return "shell_git"
        if "docker" in inp or "docker-compose" in inp:
            return "shell_docker"
        return "shell_generic"
    if tn in ("anysearch", "search_web", "web_search", "honeycomb", "hive_mind") or "search" in tn:
        return "search"
    if "knowledge" in tn or "remember" in tn or "experience" in tn:
        return "knowledge_op"
    return "other"


def build_timeline(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """从 trace JSONL 构建可分析的时间线。"""
    init_entry = {}
    close_entry = {}
    tool_calls = []
    llm_calls = []
    phases = []
    knowledge_hits = []

    for e in entries:
        t = e.get("_type", e.get("type", ""))
        if t == "init":
            init_entry = e
        elif t == "close":
            close_entry = e
        elif t == "tool_call":
            # 归一化字段：trace JSONL 用 input/output，我们统一用 input_digest/output_digest
            e["tool_name"] = e.get("tool_name", e.get("tool", "?"))
            e["input_digest"] = e.get("input_digest", e.get("input", ""))
            e["output_digest"] = e.get("output_digest", e.get("output", ""))
            tool_calls.append(e)
        elif t == "llm_call":
            llm_calls.append(e)
        elif t == "phase":
            phases.append(e)
        elif t == "knowledge_hit":
            knowledge_hits.append(e)

    # 计算工具调用类别分布
    category_dist = {}
    for tc in tool_calls:
        cat = _classify_tool_call(tc.get("tool_name", ""), tc.get("input_digest", ""))
        category_dist[cat] = category_dist.get(cat, 0) + 1

    # 统计失败
    errors = [tc for tc in tool_calls if tc.get("status") == "error"]
    error_reasons = {}
    for err in errors:
        reason = err.get("error", "unknown")[:100]
        error_reasons[reason] = error_reasons.get(reason, 0) + 1

    # 性能
    total_duration = close_entry.get("elapsed", 0)
    llm_duration_sum = sum(llm.get("duration_ms", 0) for llm in llm_calls)
    tool_duration_sum = sum(tc.get("duration_ms", 0) for tc in tool_calls)

    return {
        "trace_id": init_entry.get("task_id", "?"),
        "description": init_entry.get("description", "")[:200],
        "status": close_entry.get("status", "?"),
        "error": close_entry.get("error", ""),
        "total_elapsed": total_duration,
        "tool_calls_count": len(tool_calls),
        "llm_calls_count": len(llm_calls),
        "phases_count": len(phases),
        "knowledge_hits_count": len(knowledge_hits),
        "error_count": len(errors),
        "category_distribution": category_dist,
        "error_reasons": error_reasons,
        "performance_ms": {
            "total": round(total_duration * 1000, 1),
            "llm": round(llm_duration_sum, 1),
            "tool": round(tool_duration_sum, 1),
            "llm_per_call_avg": round(llm_duration_sum / max(len(llm_calls), 1), 1),
            "overhead": round(total_duration * 1000 - llm_duration_sum - tool_duration_sum, 1),
        },
        "tool_calls": tool_calls,
        "llm_calls": llm_calls,
    }


# ═══════════════════════════════════════════════════════════
#  2. 转折点识别 — 找到决策分歧的关键步骤
# ═══════════════════════════════════════════════════════════


def _build_error_signature(err: dict[str, Any]) -> str:
    """提取错误的模式签名（模糊匹配用）。

    exec_command 失败但不同命令参数 → 识别为同一类问题
    """
    error_text = err.get("error", "")
    tool_name = err.get("tool_name", "")

    # 核心：错误文本中提取关键词
    signatures = []

    if "unbound variable" in error_text or "QCLAW_CLI_NODE_BINARY" in error_text:
        signatures.append("qclaw_node_wrapper_broken")
    if "command not found" in error_text or "not found" in error_text:
        signatures.append("command_not_found")
    if "找不到" in error_text or "no matches found" in error_text:
        signatures.append("glob_no_match")
    if "timed out" in error_text or "超时" in error_text or "timeout" in error_text.lower():
        signatures.append("timeout")
    if "429" in error_text or "quota" in error_text or "limit" in error_text:
        signatures.append("api_rate_limit")
    if "401" in error_text or "unauthorized" in error_text.lower() or "Authorization" in error_text:
        signatures.append("api_auth_error")
    if "permission denied" in error_text.lower():
        signatures.append("permission_denied")

    if not signatures:
        # 默认用工具名+首句作为签名
        first_sentence = error_text.split(".")[0][:40] if error_text else "unknown"
        signatures.append(f"{tool_name}:{first_sentence}")

    return "|".join(signatures)


def find_turning_points(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """识别时间线中的决策转折点。

    转折点类型：
    1. 同一错误连续出现 → 继续深挖还是换道
    2. 工具类别突变（从路径搜索突然切换到知识查找）
    3. 连续失败后 LLM 调用时间大幅增加（说明LLM在纠结）
    4. 长时间无工具调用（LLM在独立思考）
    """
    tool_calls = timeline.get("tool_calls", [])

    # 统计错误模式聚集
    error_clusters = {}  # signature → [steps]
    prev_category = None
    category_change_steps = []

    for i, tc in enumerate(tool_calls):
        step = tc.get("step", i)
        tool_name = tc.get("tool_name", tc.get("tool", "?"))
        category = _classify_tool_call(tool_name, tc.get("input_digest", ""))

        # 类别突变检测
        if prev_category and category != prev_category:
            category_change_steps.append(
                {
                    "step": step,
                    "from": prev_category,
                    "to": category,
                }
            )
        prev_category = category

        # 错误聚集检测
        if tc.get("status") == "error":
            signature = _build_error_signature(tc)
            if signature not in error_clusters:
                error_clusters[signature] = []
            error_clusters[signature].append(step)

    # 提取错误模式
    patterns = []
    for sig, steps in error_clusters.items():
        if len(steps) >= _MIN_PATTERN_OCCURRENCES:
            # 检查是否连续出现（间隔 ≤ _ERROR_SIGNATURE_MERGE_WINDOW 步）
            is_clustered = all(steps[j + 1] - steps[j] <= _ERROR_SIGNATURE_MERGE_WINDOW for j in range(len(steps) - 1))
            patterns.append(
                {
                    "signature": sig,
                    "occurrences": len(steps),
                    "steps": steps,
                    "is_clustered": is_clustered,
                    "type": "repeated_error",
                }
            )

    # 多步同一错误后是否换道？
    # 这里返回最有价值的信息
    return {
        "repeated_error_patterns": patterns,
        "category_transitions": category_change_steps,
    }


# ═══════════════════════════════════════════════════════════
#  3. 复盘分析 — 生成结构化复盘报告
# ═══════════════════════════════════════════════════════════


def analyze_trace(trace_id: str) -> dict[str, Any]:
    """对一次 trace 做完整复盘分析，返回结构化分析结果。"""
    entries = read_trace(trace_id)
    if not entries:
        return {"status": "no_data", "trace_id": trace_id}

    timeline = build_timeline(entries)
    turning = find_turning_points(timeline)

    analysis = {
        "trace_id": trace_id,
        "overview": {
            "status": timeline["status"],
            "description": timeline["description"],
            "elapsed_s": round(timeline["total_elapsed"], 1),
            "tool_calls": timeline["tool_calls_count"],
            "llm_calls": timeline["llm_calls_count"],
            "errors": timeline["error_count"],
        },
        "performance": timeline["performance_ms"],
        "patterns": turning.get("repeated_error_patterns", []),
        "category_transitions": turning.get("category_transitions", []),
        "category_distribution": timeline["category_distribution"],
        "error_reasons": timeline["error_reasons"],
        "insights": [],  # 留给 review() 填充
        "experiences": [],  # 留给 review() 填充
    }

    # 自动生成洞察
    insights = []

    # 洞察1：重复错误模式
    for pat in analysis["patterns"]:
        if pat["is_clustered"] and pat["occurrences"] >= 2:
            sig = pat["signature"]
            count = pat["occurrences"]
            steps = pat["steps"]

            # 根据签名生成可读的洞察
            if "qclaw_node_wrapper_broken" in sig:
                insights.append(
                    {
                        "type": "blocker_not_bypassed",
                        "severity": "high",
                        "summary": f"QClaw node wrapper 错误出现 {count} 次（step {steps[0]}–{steps[-1]}），每次都换命令重试但未换渠道绕过去",
                        "suggestion": "直接使用 exec_safe 调用 node，或在 tool 代码中 hardcode 真实 node 路径绕过 wrapper",
                        "steps": steps,
                    }
                )
            elif "timeout" in sig:
                insights.append(
                    {
                        "type": "timeout_chain",
                        "severity": "medium",
                        "summary": f"超时 {count} 次，可能命令涉及大量文件遍历或大文件传输",
                        "suggestion": "缩小搜索范围或增加 timeout 参数，或改用更精确的路径",
                        "steps": steps,
                    }
                )
            else:
                insights.append(
                    {
                        "type": "repeated_error",
                        "severity": "medium",
                        "summary": f"同一错误模式出现 {count} 次（steps: {steps[:6]}），没有换方案",
                        "suggestion": "识别到同一类问题持续失败时，考虑完全不同的实现方式",
                        "steps": steps,
                    }
                )

    # 洞察2：切换太晚
    if timeline["error_count"] >= 3 and timeline["tool_calls_count"] >= 8:
        first_error_step = None
        for tc in timeline["tool_calls"]:
            if tc.get("status") == "error":
                first_error_step = tc.get("step", 0)
                break
        if first_error_step:
            remaining = timeline["tool_calls_count"] - first_error_step
            if remaining >= 5:
                insights.append(
                    {
                        "type": "late_switch",
                        "severity": "high",
                        "summary": f"第一个错误出现在 step {first_error_step}，但之后又进行了 {remaining} 次工具调用才放弃",
                        "suggestion": "出现阻塞性错误并尝试 2-3 次不同方案后仍未解决 → 上报问题并记录 blocker",
                        "steps": list(range(first_error_step, first_error_step + min(remaining, 10))),
                    }
                )

    # 洞察3：一直在查没在干活
    dist = analysis["category_distribution"]
    search_count = dist.get("shell_path_search", 0) + dist.get("shell_node", 0)
    total = timeline["tool_calls_count"]
    if total > 0 and search_count / total > 0.4:
        insights.append(
            {
                "type": "search_loop",
                "severity": "medium",
                "summary": f"路径/环境检查占全部工具调用的 {search_count}/{total}（{search_count * 100 // total}%），可能陷入'找东西'而非'干事情'",
                "suggestion": "环境检查应该专注于验证关键路径是否可用，而不是贪婪方式地毯式搜索",
                "steps": [],
            }
        )

    # 洞察4：忘了正事 — 环境检查占大多数且实际没产出
    if total >= 5 and search_count / total >= 0.5 and timeline["error_count"] > 0:
        has_result = any(
            tc.get("tool_name", tc.get("tool", "")) in ("file_write", "send_text", "send_file")
            for tc in timeline["tool_calls"]
        )
        if not has_result:
            insights.append(
                {
                    "type": "forgot_main_task",
                    "severity": "high",
                    "summary": f"{total} 次工具调用中 {search_count} 次是环境检查，{'从未' if not has_result else '没有明显'}产出。任务描述是：「{timeline.get('description', '')[:60]}」，但实际行为被环境排查消耗干净",
                    "suggestion": "环境检查应在 3-5 次调用内收敛到一个结论：要么可用直接干，要么不可用换道。用 exec_safe(node xxx) 跳过 wrapper 是最快捷径",
                    "steps": [],
                }
            )

    analysis["insights"] = insights
    return analysis


# ═══════════════════════════════════════════════════════════
#  4. 经验提炼 — 从复盘洞察中生成可沉淀的经验
# ═══════════════════════════════════════════════════════════


def distill_experiences(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """从复盘分析中提取可沉淀的经验条目（给 experience engine）。"""
    experiences = []

    for insight in analysis.get("insights", []):
        if insight.get("severity") != "high":
            continue

        exp = {
            "type": "lesson",
            "summary": insight["summary"],
            "suggestion": insight.get("suggestion", ""),
            "context": analysis["overview"]["description"],
            "confidence": 0.7 if insight["type"] == "blocker_not_bypassed" else 0.6,
            "source_trace": analysis["trace_id"],
            "meta_pattern": "工具使用",
            "when_to_use": "遇到与本次 trace 相同类型的错误模式时",
            "when_to_ignore": "同一个工具的问题但错误文本完全不同时",
        }
        experiences.append(exp)

    return experiences


# ═══════════════════════════════════════════════════════════
#  5. 主函数：review() — Agent 自己调用的复盘入口
# ═══════════════════════════════════════════════════════════


def review(trace_id: str) -> dict[str, Any]:
    """Agent 调用的复盘入口。

    用法示例（在告别回复中）：
    ```python
    from lib.trace_review import review
    trace_id = current_trace.task_id
    review_result = review(trace_id)
    if review_result["experiences"]:
        for exp in review_result["experiences"]:
            note_write(exp["summary"], ...)  # 沉淀到经验层
    ```

    Returns:
        包含复盘分析和待沉淀经验的字典
    """
    analysis = analyze_trace(trace_id)
    experiences = distill_experiences(analysis)

    result = {
        **analysis,
        "experiences": experiences,
        "experiences_count": len(experiences),
        "review_generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 写复盘报告到 reviews 目录（纯数据，供后续交叉分析）
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REVIEW_DIR / f"{trace_id}.json"
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("trace_review: 写入复盘报告失败 %s: %s", trace_id, e)

    return result


# ═══════════════════════════════════════════════════════════
#  6. 跨 trace 分析 — 发现长期模式
# ═══════════════════════════════════════════════════════════


def cross_analyze(trace_ids: list[str] = None, limit: int = 20) -> dict[str, Any]:
    """分析最近的多次 trace，发现重复出现的模式。

    Args:
        trace_ids: 指定的 trace ID 列表（为空则扫描最近的 limit 个）
        limit: 最多分析的 trace 数量

    Returns:
        跨 trace 分析报告
    """
    if not trace_ids:
        # 扫描最近的 traces
        if not TRACE_DIR.exists():
            return {"status": "no_data"}
        files = sorted(TRACE_DIR.glob("*.jsonl"), key=os.path.getmtime, reverse=True)
        trace_ids = [f.stem for f in files[:limit]]

    all_analyses = []
    all_patterns = {}
    failed_traces = []

    for tid in trace_ids:
        analysis = analyze_trace(tid)
        if analysis.get("overview", {}).get("status") == "no_data":
            continue
        all_analyses.append(analysis)

        if analysis["overview"]["status"] == "failed":
            failed_traces.append(tid)

        for pat in analysis.get("patterns", []):
            sig = pat["signature"]
            if sig not in all_patterns:
                all_patterns[sig] = []
            all_patterns[sig].append(tid)

    # 跨 trace 总结
    summary = {
        "analyzed_count": len(all_analyses),
        "failed_count": len(failed_traces),
        "success_count": len(all_analyses) - len(failed_traces),
        "status": "completed",
        "recurring_patterns": [
            {"signature": sig, "affected_traces": tids, "count": len(tids)}
            for sig, tids in sorted(all_patterns.items(), key=lambda x: -len(x[1]))
            if len(tids) >= 2
        ],
        "failed_traces": failed_traces,
    }

    return summary
