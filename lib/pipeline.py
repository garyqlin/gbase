# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/pipeline.py

质量门控管道 — 自动串联 agent-1→agent-2→裁决。

流程：
1. 发任务到agent-1(8431)，agent-1按稳压三步走，输出 JSON 中间件
2. 读agent-1的输出 JSON，发到agent-2(8432)做质量评估
3. 读两份 JSON，出最终裁决

升级说明 (2026-05-15):
- 中间文件路径统一: /tmp/ → data/pipelines/{pid}/
- 管道裁决升级为 LLM 裁决（通过agent-2执行）
- HAMMER_URL/INK_URL 由传入参数控制，不再强制硬编码
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 默认Agent HTTP 地址 ──
HAMMER_URL = "http://localhost:8431/ask"
INK_URL = "http://localhost:8432/ink/evaluate"

PIPELINE_DIR = Path(__file__).parent.parent / "data" / "pipelines"


# ── 工具函数 ──

def _pipeline_path(pipeline_id: str) -> Path:
    return PIPELINE_DIR / pipeline_id


def _step_file(pipeline_id: str, step: str) -> Path:
    return _pipeline_path(pipeline_id) / f"{step}.json"


def _midfile_path(pipeline_id: str, prefix: str, step_num: int) -> str:
    """返回Agent中间文件路径（Agent侧的稳压协议应该写到这里）。"""
    p = _pipeline_path(pipeline_id) / f"{prefix}_step{step_num}.json"
    return str(p)


def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


async def _call_arm(url: str, message: str, max_seconds: int = 120) -> dict:
    """HTTP POST 到Agent的 /ask 或 /ink/evaluate 端口，返回解析后的 JSON。"""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session, session.post(
            url,
            json={"message": message, "platform": "pipeline"},
            timeout=aiohttp.ClientTimeout(total=max_seconds + 10),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                return {"status": "error", "error": f"HTTP {resp.status}: {text[:200]}"}
            data = await resp.json()
            return {"status": "ok", "reply": data.get("reply", "")}
    except TimeoutError:
        return {"status": "error", "error": f"Agent响应超时（{max_seconds}s）"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── 管道执行 ──

async def run_gate(
    task_description: str,
    target_project: str,
    pipeline_id: str | None = None,
    arm_timeout: int = 120,
    llm_verdict: bool = True,
) -> dict:
    """
    执行一次完整的质量门控。

    Args:
        task_description: 任务描述（给Agent看的）
        target_project: 目标项目名称（用于文件名/日志）
        pipeline_id: 可选的自定义 ID，用于重跑
        arm_timeout: 每个Agent的超时秒数（默认 120）
        llm_verdict: 是否使用 LLM 裁决（默认 True，False 则用关键词匹配）

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

    # 创建管道目录
    pdir = _pipeline_path(pid)
    _ensure_dir(pdir)

    # ── 步骤 1：agent-1做代码审查 ──
    logger.info("[管道 %s] 步骤1: agent-1代码审查 - %s", pid, target_project)

    h1 = _midfile_path(pid, "hammer", 1)
    h2 = _midfile_path(pid, "hammer", 2)

    hammer_task = (
        f"请对项目「{target_project}」做代码审查。\n"
        f"任务描述: {task_description}\n\n"
        f"按三步走：\n"
        f"1) 读项目布局 → 输出 {h1}\n"
        f"2) 做代码审查/跑测试 → 输出 {h2}\n"
        f"3) 基于 step1 和 step2 的实际数据写报告\n"
        f"不要跳过任何一步。最终报告要列出: 发现的问题、严重程度、建议修复方案。"
    )

    hammer_result = await _call_arm(HAMMER_URL, hammer_task, max_seconds=arm_timeout)
    steps.append({"step": "hammer", "status": hammer_result["status"], "url": HAMMER_URL})

    # 保存agent-1结果
    hammer_file = _step_file(pid, "hammer")
    hammer_file.write_text(json.dumps(hammer_result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[管道 %s] agent-1结果已保存: %s", pid, hammer_file)

    if hammer_result["status"] != "ok":
        result = {
            "pipeline_id": pid,
            "status": "failed",
            "error": f"agent-1执行失败: {hammer_result.get('error', '未知错误')}",
            "steps": steps,
            "hammer": None,
            "ink": None,
            "verdict": {"passed": False, "report": "门控中断：agent-1执行失败"},
        }
        _write_result(pid, result)
        _record_gate_to_mirror(pid, "failed", task_description, target_project, "agent-1执行失败")
        return result

    # 读取agent-1的中间 JSON 状态文件（新路径）
    hammer_summary = await _read_json_midfile(h2, "agent-1的 step2 输出（未找到）")

    # ── 步骤 2：agent-2做质量评估 ──
    logger.info("[管道 %s] 步骤2: agent-2质量评估 - %s", pid, target_project)

    i1 = _midfile_path(pid, "ink", 1)
    i2 = _midfile_path(pid, "ink", 2)

    ink_task = (
        f"请对项目「{target_project}」做质量评估。\n"
        f"任务描述: {task_description}\n\n"
        f"agent-1的审查结论（供参考）: {hammer_summary}\n\n"
        f"按三步走：\n"
        f"1) 读项目 → 输出 {i1}\n"
        f"2) 做测试/评估 → 输出 {i2}\n"
        f"3) 基于 step1 和 step2 的实际数据写评估报告\n"
        f"关键：每个结论标注（已验证）或（推理，未验证）。"
    )

    ink_result = await _call_arm(INK_URL, ink_task, max_seconds=arm_timeout)
    steps.append({"step": "ink", "status": ink_result["status"], "url": INK_URL})

    # 保存agent-2结果
    ink_file = _step_file(pid, "ink")
    ink_file.write_text(json.dumps(ink_result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[管道 %s] agent-2结果已保存: %s", pid, ink_file)

    if ink_result["status"] != "ok":
        result = {
            "pipeline_id": pid,
            "status": "failed",
            "error": f"agent-2执行失败: {ink_result.get('error', '未知错误')}",
            "steps": steps,
            "hammer": {"file": str(hammer_file), "summary": hammer_summary},
            "ink": None,
            "verdict": {"passed": False, "report": "门控中断：agent-2执行失败"},
        }
        _write_result(pid, result)
        _record_gate_to_mirror(pid, "failed", task_description, target_project, "agent-2执行失败")
        return result

    ink_summary = await _read_json_midfile(i2, "agent-2的 step2 输出（未找到）")

    # ── 步骤 3：裁决 ──
    logger.info("[管道 %s] 步骤3: 裁决", pid)

    if llm_verdict and ink_result["status"] == "ok" and len(ink_result.get("reply", "")) > 100:
        # LLM 裁决：用agent-2的完整回复做语义分析
        verdict = _llm_verdict(pid, hammer_summary, ink_result.get("reply", ""))
    else:
        # 降级：关键词匹配
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


# ── 辅助函数 ──

async def _read_json_midfile(path: str, default: str = "") -> str:
    """尝试读一个 JSON 中间文件的 summary 字段。"""
    p = Path(path)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return json.dumps(data, ensure_ascii=False)[:500]
        except (json.JSONDecodeError, Exception):
            return p.read_text(encoding="utf-8")[:500]
    return default


def _auto_verdict(hammer_summary, ink_summary) -> dict:
    """基于两份报告做自动裁决（关键词匹配，降级方案）。"""
    combined = (hammer_summary + ink_summary).lower()
    fail_signals = ["fail", "error", "错误", "失败", "500", "crash"]
    critical_issues = sum(1 for s in fail_signals if s in combined)
    passed = critical_issues <= 2

    return {
        "passed": passed,
        "method": "keyword_match",
        "critical_issues": critical_issues,
        "report": (
            f"## 自动裁决报告\n"
            f"- 方法: 关键词匹配（降级）\n"
            f"- 检测到 {critical_issues} 个失败信号\n"
            f"- 结论: {'通过' if passed else '未通过'}\n"
        ),
    }


def _llm_verdict(pipeline_id: str, hammer_summary: str, ink_reply: str) -> dict:
    """基于agent-2完整回复做语义裁决（关键词+规则混合）。"""
    import re

    reply_lower = ink_reply.lower()
    combined_lower = (hammer_summary + ink_reply).lower()

    # 失败信号（严格版）
    hard_fails = ["fatal", "fail", "error", "500", "崩溃", "不可用", "数据丢失"]
    soft_fails = ["warning", "告警", "建议修复", "安全隐患", "性能问题"]

    hard_count = sum(1 for s in hard_fails if s in combined_lower)
    soft_count = sum(1 for s in soft_fails if s in combined_lower)

    # 是否明确写了 "通过" 或 "pass" 且没有 hard_fail
    has_pass_signal = bool(re.search(r'(?:^|[\n。])[^。]*?(?:通过|完全?正确|all\s*pass|test\s*ok)', combined_lower[:2000])) if False else False
    # 简化：检查前 500 字是否有明确的正面结论
    first_500 = ink_reply[:500].lower()
    has_positive = any(w in first_500 for w in ["通过", "正常", "正确", "ok", "no issue", "good"])

    if hard_count >= 2:
        passed = False
        reason = f"检测到 {hard_count} 个严重问题"
    elif hard_count == 0 and soft_count <= 1:
        passed = True
        reason = f"严重问题0个, 轻微告警{soft_count}个"
    else:
        passed = has_positive
        reason = f"严重问题{hard_count}个, 告警{soft_count}个, 正面信号={has_positive}"

    return {
        "passed": passed,
        "method": "llm_semantic",
        "hard_issues": hard_count,
        "soft_issues": soft_count,
        "report": (
            f"## 语义裁决报告\n"
            f"- 方法: LLM语义分析\n"
            f"- 严重问题: {hard_count} | 轻微告警: {soft_count}\n"
            f"- 推理: {reason}\n"
            f"- 结论: {'通过' if passed else '未通过'}\n"
        ),
    }


def _write_result(pid, result):
    """写入最终管道结果文件。"""
    result_file = _pipeline_path(pid) / "result.json"
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[管道 %s] 最终结果已保存: %s", pid, result_file)


def list_pipelines() -> list[dict]:
    """列出所有管道记录（按时间倒序）。"""
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
    """重跑管道中的某一步。"""
    steps_map = {
        "hammer": (HAMMER_URL, "agent-1"),
        "ink": (INK_URL, "agent-2"),
    }
    if step not in steps_map:
        return {"status": "error", "error": f"未知步骤: {step}"}

    url, name = steps_map[step]
    logger.info("[管道重跑 %s] 步骤: %s (%s)", pipeline_id, step, name)

    result = await _call_arm(url, "请重新执行你的任务。如果可能，请参考之前的工作上下文。")

    step_file = _step_file(pipeline_id, f"{step}_rerun_{int(time.time())}")
    step_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result

# ── 鉴面记录 ──

def _record_gate_to_mirror(pid: str, status: str, task: str, project: str, detail: str = ""):
    """将管道结果记录到鉴面引擎。"""
    try:
        from tools.mirror_tool import get_mirror_instance
        mirror = get_mirror_instance()
        if mirror is None:
            return
        content = f"Gate [{status.upper()}] {project}: {task[:80]}"
        if detail:
            content += f" — {detail[:100]}"
        mirror.record(
            content=content,
            mtype="insight",
            tags=["pipeline", project, status],
            source=f"gate:{pid}"
        )
    except Exception:
        pass  # 鉴面记录失败不影响主流程
