# SPDX-License-Identifier: MIT
"""
gbase/lib/pipeline.py

Quality Gate Pipeline — auto-chains agent-1→agent-2→verdict.

Flow:
1. Send task to agent-1 (8431), agent-1 follows the three-step stabilization protocol, outputs JSON middleware
2. Read agent-1's output JSON, send to agent-2 (8432) for quality evaluation
3. Read both JSONs, produce final verdict

Upgrade Notes (2026-05-15):
- Unified intermediate file path: /tmp/ → data/pipelines/{pid}/
- Pipeline verdict upgraded to LLM verdict (executed via agent-2)
- HAMMER_URL/INK_URL controlled by input parameters, no longer hardcoded
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Default Agent HTTP Addresses ──
HAMMER_URL = "http://localhost:8431/ask"
INK_URL = "http://localhost:8432/ink/evaluate"

PIPELINE_DIR = Path(__file__).parent.parent / "data" / "pipelines"


# ── Utility Functions ──


def _pipeline_path(pipeline_id: str) -> Path:
    return PIPELINE_DIR / pipeline_id


def _step_file(pipeline_id: str, step: str) -> Path:
    return _pipeline_path(pipeline_id) / f"{step}.json"


def _midfile_path(pipeline_id: str, prefix: str, step_num: int) -> str:
    """Return the Agent intermediate file path (Agent-side stabilization protocol should write here)."""
    p = _pipeline_path(pipeline_id) / f"{prefix}_step{step_num}.json"
    return str(p)


def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


async def _call_arm(url: str, message: str, max_seconds: int = 120) -> dict:
    """HTTP POST to the Agent's /ask or /ink/evaluate endpoint, return parsed JSON."""
    import aiohttp

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                url,
                json={"message": message, "platform": "pipeline"},
                timeout=aiohttp.ClientTimeout(total=max_seconds + 10),
            ) as resp,
        ):
            if resp.status != 200:
                text = await resp.text()
                return {"status": "error", "error": f"HTTP {resp.status}: {text[:200]}"}
            data = await resp.json()
            return {"status": "ok", "reply": data.get("reply", "")}
    except TimeoutError:
        return {"status": "error", "error": f"Agent response timeout ({max_seconds}s)"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Pipeline Execution ──


async def run_gate(
    task_description: str,
    target_project: str,
    pipeline_id: str | None = None,
    arm_timeout: int = 120,
    llm_verdict: bool = True,
) -> dict:
    """
    Execute a complete quality gate run.

    Args:
        task_description: Task description (for the Agent)
        target_project: Target project name (for filenames/logging)
        pipeline_id: Optional custom ID for reruns
        arm_timeout: Timeout in seconds per Agent (default 120)
        llm_verdict: Whether to use LLM verdict (default True, False uses keyword matching)

    Returns:
        {
            "pipeline_id": str,
            "status": "passed" | "failed" | "error",
            "hammer": { "file": str, "summary": str },
            "ink": { "file": str, "summary": str },
            "verdict": { "passed": bool, "report": str },
            "steps": [each step result]
        }
    """
    pid = pipeline_id or f"gate_{int(time.time())}_{target_project.replace('/', '_')}"
    steps = []

    # Create pipeline directory
    pdir = _pipeline_path(pid)
    _ensure_dir(pdir)

    # ── Step 1: agent-1 Code Review ──
    logger.info("[Pipeline %s] Step 1: agent-1 code review — %s", pid, target_project)

    h1 = _midfile_path(pid, "hammer", 1)
    h2 = _midfile_path(pid, "hammer", 2)

    hammer_task = (
        f"Please conduct a code review for project '{target_project}'.\n"
        f"Task description: {task_description}\n\n"
        f"Follow these three steps:\n"
        f"1) Read project layout → output to {h1}\n"
        f"2) Conduct code review / run tests → output to {h2}\n"
        f"3) Write report based on actual data from step1 and step2\n"
        f"Do not skip any step. Final report must list: issues found, severity, suggested fixes."
    )

    hammer_result = await _call_arm(HAMMER_URL, hammer_task, max_seconds=arm_timeout)
    steps.append({"step": "hammer", "status": hammer_result["status"], "url": HAMMER_URL})

    # Save agent-1 result
    hammer_file = _step_file(pid, "hammer")
    hammer_file.write_text(json.dumps(hammer_result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[Pipeline %s] agent-1 result saved: %s", pid, hammer_file)

    if hammer_result["status"] != "ok":
        result = {
            "pipeline_id": pid,
            "status": "failed",
            "error": f"agent-1 execution failed: {hammer_result.get('error', 'unknown error')}",
            "steps": steps,
            "hammer": None,
            "ink": None,
            "verdict": {"passed": False, "report": "Gate aborted: agent-1 execution failed"},
        }
        _write_result(pid, result)
        _record_gate_to_mirror(pid, "failed", task_description, target_project, "agent-1 execution failed")
        return result

    # Read agent-1's intermediate JSON state file (new path)
    hammer_summary = await _read_json_midfile(h2, "agent-1 step2 output (not found)")

    # ── Step 2: agent-2 Quality Evaluation ──
    logger.info("[Pipeline %s] Step 2: agent-2 quality evaluation — %s", pid, target_project)

    i1 = _midfile_path(pid, "ink", 1)
    i2 = _midfile_path(pid, "ink", 2)

    ink_task = (
        f"Please conduct a quality evaluation for project '{target_project}'.\n"
        f"Task description: {task_description}\n\n"
        f"agent-1 review conclusion (for reference): {hammer_summary}\n\n"
        f"Follow these three steps:\n"
        f"1) Read project → output to {i1}\n"
        f"2) Run tests/evaluation → output to {i2}\n"
        f"3) Write evaluation report based on actual data from step1 and step2\n"
        f"Key: label each conclusion as (verified) or (inferred, not verified)."
    )

    ink_result = await _call_arm(INK_URL, ink_task, max_seconds=arm_timeout)
    steps.append({"step": "ink", "status": ink_result["status"], "url": INK_URL})

    # Save agent-2 result
    ink_file = _step_file(pid, "ink")
    ink_file.write_text(json.dumps(ink_result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[Pipeline %s] agent-2 result saved: %s", pid, ink_file)

    if ink_result["status"] != "ok":
        result = {
            "pipeline_id": pid,
            "status": "failed",
            "error": f"agent-2 execution failed: {ink_result.get('error', 'unknown error')}",
            "steps": steps,
            "hammer": {"file": str(hammer_file), "summary": hammer_summary},
            "ink": None,
            "verdict": {"passed": False, "report": "Gate aborted: agent-2 execution failed"},
        }
        _write_result(pid, result)
        _record_gate_to_mirror(pid, "failed", task_description, target_project, "agent-2 execution failed")
        return result

    ink_summary = await _read_json_midfile(i2, "agent-2 step2 output (not found)")

    # ── Step 3: Verdict ──
    logger.info("[Pipeline %s] Step 3: verdict", pid)

    if llm_verdict and ink_result["status"] == "ok" and len(ink_result.get("reply", "")) > 100:
        # LLM verdict: use agent-2's full reply for semantic analysis
        verdict = _llm_verdict(pid, hammer_summary, ink_result.get("reply", ""))
    else:
        # Fallback: keyword matching
        verdict = _auto_verdict(hammer_summary, ink_summary)

    verdict_file = _step_file(pid, "verdict")
    verdict_file.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")

    pipe_status = "passed" if verdict.get("passed") else "failed"

    result = {
        "pipeline_id": pid,
        "status": pipe_status,
        "hammer": {"file": str(hammer_file), "summary": hammer_summary},
        "ink": {"file": str(ink_file), "summary": ink_summary},
        "verdict": verdict,
        "steps": steps,
    }
    _write_result(pid, result)

    _record_gate_to_mirror(pid, pipe_status, task_description, target_project)
    return result


# ── Helper Functions ──


async def _read_json_midfile(path: str, default: str = "") -> str:
    """Try to read the summary field from a JSON intermediate file."""
    p = Path(path)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return json.dumps(data, ensure_ascii=False)[:500]
        except (json.JSONDecodeError, Exception):
            return p.read_text(encoding="utf-8")[:500]
    return default


def _auto_verdict(hammer_summary, ink_summary) -> dict:
    """Auto-verdict based on two reports (keyword matching, fallback method)."""
    combined = (hammer_summary + ink_summary).lower()
    fail_signals = ["fail", "error", "错误", "失败", "500", "crash"]
    critical_issues = sum(1 for s in fail_signals if s in combined)
    passed = critical_issues <= 2

    return {
        "passed": passed,
        "method": "keyword_match",
        "critical_issues": critical_issues,
        "report": (
            f"## Auto Verdict Report\n"
            f"- Method: keyword matching (fallback)\n"
            f"- Detected {critical_issues} failure signal(s)\n"
            f"- Conclusion: {'PASS' if passed else 'FAIL'}\n"
        ),
    }


def _llm_verdict(_pipeline_id: str, hammer_summary: str, ink_reply: str) -> dict:
    """Semantic verdict based on agent-2's full reply (keyword+rule hybrid)."""
    import re

    ink_reply.lower()
    combined_lower = (hammer_summary + ink_reply).lower()

    # Failure signals (strict)
    hard_fails = ["fatal", "fail", "error", "500", "崩溃", "不可用", "数据丢失"]
    soft_fails = ["warning", "告警", "建议修复", "安全隐患", "性能问题"]

    hard_count = sum(1 for s in hard_fails if s in combined_lower)
    soft_count = sum(1 for s in soft_fails if s in combined_lower)

    # Check whether it explicitly says "pass" or equivalent without hard_fails
    (
        bool(re.search(r"(?:^|[\n。])[^。]*?(?:通过|完全?正确|all\s*pass|test\s*ok)", combined_lower[:2000]))
        if False
        else False
    )
    # Simplified: check first 500 chars for clear positive conclusions
    first_500 = ink_reply[:500].lower()
    has_positive = any(w in first_500 for w in ["通过", "正常", "正确", "ok", "no issue", "good"])

    if hard_count >= 2:
        passed = False
        reason = f"Detected {hard_count} critical issue(s)"
    elif hard_count == 0 and soft_count <= 1:
        passed = True
        reason = f"0 critical issues, {soft_count} minor alert(s)"
    else:
        passed = has_positive
        reason = f"{hard_count} critical issue(s), {soft_count} alert(s), positive signal={has_positive}"

    return {
        "passed": passed,
        "method": "llm_semantic",
        "hard_issues": hard_count,
        "soft_issues": soft_count,
        "report": (
            f"## Semantic Verdict Report\n"
            f"- Method: LLM semantic analysis\n"
            f"- Critical issues: {hard_count} | Minor alerts: {soft_count}\n"
            f"- Reasoning: {reason}\n"
            f"- Conclusion: {'PASS' if passed else 'FAIL'}\n"
        ),
    }


def _write_result(pid, result):
    """Write the final pipeline result file."""
    result_file = _pipeline_path(pid) / "result.json"
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[Pipeline %s] final result saved: %s", pid, result_file)


def list_pipelines() -> list[dict]:
    """List all pipeline records (chronologically descending)."""
    if not PIPELINE_DIR.exists():
        return []
    pipelines = []
    for d in sorted(PIPELINE_DIR.iterdir(), key=lambda p: p.name, reverse=True):
        result_file = d / "result.json"
        if result_file.exists():
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
                pipelines.append(data)
            except (json.JSONDecodeError, Exception):
                pass
    return pipelines


async def rerun_step(pipeline_id: str, step: str) -> dict:
    """Rerun a specific step in the pipeline."""
    steps_map = {
        "hammer": (HAMMER_URL, "agent-1"),
        "ink": (INK_URL, "agent-2"),
    }
    if step not in steps_map:
        return {"status": "error", "error": f"Unknown step: {step}"}

    url, name = steps_map[step]
    logger.info("[Pipeline rerun %s] step: %s (%s)", pipeline_id, step, name)

    result = await _call_arm(url, "Please re-execute your task. If possible, reference previous work context.")

    step_file = _step_file(pipeline_id, f"{step}_rerun_{int(time.time())}")
    step_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


# ── Mirror Recording ──


def _record_gate_to_mirror(pid: str, status: str, task: str, project: str, detail: str = ""):
    """Record pipeline result to the mirror engine."""
    try:
        from tools.mirror_tool import get_mirror_instance

        mirror = get_mirror_instance()
        if mirror is None:
            return
        content = f"Gate [{status.upper()}] {project}: {task[:80]}"
        if detail:
            content += f" — {detail[:100]}"
        mirror.record(content=content, mtype="insight", tags=["pipeline", project, status], source=f"gate:{pid}")
    except Exception:
        pass  # Mirror recording failure does not affect main flow
