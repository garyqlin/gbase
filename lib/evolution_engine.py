# SPDX-License-Identifier: MIT
"""
lib/evolution_engine.py

Phase 2: auto-evolution trigger chain.

提供：
- 进化触发规则引擎 — 判定哪些改动需要触发评估
- 多角度评估 — 稳定性/性能/安全三维检查
- 自动回滚决策 — 评估不通过自动恢复
- 恢复后诊断 — 回滚后验证系统正常

设计原则：
- 可回滚（所有改动前自动备份）
- 低侵入（评估不影响正常运行）
- 渐进式（评估失败先回滚再诊断，不停机）
"""

import json
import os
import subprocess
from datetime import datetime

# ── 配置 ───────────────────────────────────────────
from pathlib import Path

from lib.backup import list_backups, restore_backup

ENGINE_DIR = os.getenv("GBASE_EVOLUTION_DIR") or str(Path(__file__).resolve().parent.parent / ".evolution")
EVAL_LOG_PATH = os.path.join(ENGINE_DIR, "evaluations.jsonl")
RULES_PATH = os.path.join(ENGINE_DIR, "rules.json")

# 默认触发规则
DEFAULT_RULES = {
    "triggers": {
        # 哪些文件改动需要评估
        "paths": [
            "/main.py",
            "/lib/",
            "/tools/",
            "/skills/",
            "/config/",
            "/systemd/",
        ],
        # 文件大小变化阈值（超过该比率触发评估）
        "size_change_ratio": 0.10,  # 10%
        # 最小改动行数（行数变化超过此值触发）
        "min_line_change": 5,
    },
    "evaluation": {
        # 稳定性检查
        "stability": {
            "enabled": True,
            "check_services": ["opprime.service"],
            "max_restart_attempts": 3,
            "startup_timeout_sec": 15,
        },
        # 性能检查
        "performance": {
            "enabled": True,
            "max_response_time_ms": 5000,
            "memory_increase_threshold_mb": 100,
        },
        # 安全检查
        "security": {
            "enabled": True,
            "forbidden_patterns": [
                "os.system(",
                "subprocess.call(",
                "eval(",
                "__import__(",
            ],
            "forbidden_imports": ["socket", "requests", "urllib"],
        },
    },
    "rollback": {
        "auto_rollback": True,  # 评估失败自动回滚
        "max_rollback_depth": 5,  # 最多回滚 5 个版本
        "diagnose_after_rollback": True,  # 回滚后自动诊断
    },
}


# ── 内部工具 ────────────────────────────────────────


def _ensure_dirs():
    os.makedirs(ENGINE_DIR, exist_ok=True)


def _load_rules() -> dict:
    _ensure_dirs()
    if os.path.exists(RULES_PATH):
        try:
            with open(RULES_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass
    # 写入默认规则
    with open(RULES_PATH, "w") as f:
        json.dump(DEFAULT_RULES, f, indent=2, ensure_ascii=False)
    return DEFAULT_RULES


def _save_rules(rules: dict):
    _ensure_dirs()
    with open(RULES_PATH, "w") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)


def _log_evaluation(entry: dict):
    _ensure_dirs()
    entry["timestamp"] = datetime.now().isoformat()
    with open(EVAL_LOG_PATH, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _count_lines(filepath: str) -> int:
    try:
        with open(filepath) as f:
            return sum(1 for _ in f)
    except (FileNotFoundError, UnicodeDecodeError):
        return 0


# ── 阶段1：触发规则引擎 ─────────────────────────────


def should_trigger_evaluation(
    filepath: str,
    old_size: int | None = None,
    new_size: int | None = None,
    old_lines: int | None = None,
    new_lines: int | None = None,
) -> tuple[bool, str]:
    """
    判断文件修改是否应触发进化评估。

    返回 (should_evaluate, reason)
    """
    rules = _load_rules()
    triggers = rules["triggers"]

    # 检查路径匹配
    path_matched = False
    for pattern in triggers["paths"]:
        if pattern in filepath:
            path_matched = True
            break

    if not path_matched:
        return False, f"路径 {filepath} 不在触发范围内"

    # 检查大小变化
    if old_size is not None and new_size is not None and old_size > 0:
        ratio = abs(new_size - old_size) / old_size
        if ratio >= triggers["size_change_ratio"]:
            return True, f"文件大小变化 {ratio:.1%}，超过阈值 {triggers['size_change_ratio']:.1%}"

    # 检查行数变化
    if old_lines is not None and new_lines is not None:
        delta = abs(new_lines - old_lines)
        if delta >= triggers["min_line_change"]:
            return True, f"行数变化 {delta}，超过阈值 {triggers['min_line_change']}"

    return False, "变化量未达触发阈值"


# ── 阶段2：多角度评估 ───────────────────────────────


def evaluate_stability() -> dict:
    """
    稳定性评估：检查主服务是否正常运行。
    返回 {passed, score, details, recommendation}
    """
    rules = _load_rules()
    cfg = rules["evaluation"]["stability"]

    if not cfg["enabled"]:
        return {"passed": True, "score": 1.0, "details": "稳定性检查已禁用"}

    services = cfg["check_services"]
    results = []

    for svc in services:
        try:
            result = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=5)
            active = result.stdout.strip() == "active"
            results.append({"service": svc, "active": active, "output": result.stdout.strip()})
        except subprocess.TimeoutExpired:
            results.append({"service": svc, "active": False, "output": "timeout"})

    all_active = all(r["active"] for r in results)
    passed = all_active

    return {
        "passed": passed,
        "score": 1.0 if passed else 0.0,
        "details": "服务状态: " + ", ".join(r["service"] + "=" + str(r["active"]) for r in results),
        "services": results,
    }


def evaluate_performance() -> dict:
    """
    性能评估：检查内存、响应时间等指标。
    返回 {passed, score, details}
    """
    rules = _load_rules()
    cfg = rules["evaluation"]["performance"]

    if not cfg["enabled"]:
        return {"passed": True, "score": 1.0, "details": "性能检查已禁用"}

    # 当前内存使用（通过 /proc 或 ps）
    try:
        result = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=5)
        mem_kb = int(result.stdout.strip()) if result.stdout.strip() else 0
        mem_mb = mem_kb / 1024.0
    except (ValueError, subprocess.TimeoutExpired):
        mem_mb = 0

    mem_threshold = cfg["memory_increase_threshold_mb"]
    passed = mem_mb < mem_threshold * 5 if mem_threshold > 0 else True

    return {
        "passed": passed,
        "score": 1.0 if passed else 0.5,
        "details": f"当前内存: {mem_mb:.1f}MB",
        "memory_mb": round(mem_mb, 1),
    }


def evaluate_security(filepath: str, content: str = "") -> dict:
    """
    安全性评估：扫描改动中是否有危险模式。
    返回 {passed, score, details, findings}
    """
    rules = _load_rules()
    cfg = rules["evaluation"]["security"]

    if not cfg["enabled"]:
        return {"passed": True, "score": 1.0, "details": "安全检查已禁用"}

    findings = []

    # 扫描内容
    if content:
        for pattern in cfg["forbidden_patterns"]:
            if pattern in content:
                findings.append(f"⚠️ 危险模式: {pattern}")

    # 扫描整个文件
    if os.path.exists(filepath):
        try:
            with open(filepath) as f:
                full_content = f.read()
            for imp in cfg["forbidden_imports"]:
                if f"import {imp}" in full_content or f"from {imp}" in full_content:
                    findings.append(f"⚠️ 禁止导入: {imp}")
        except UnicodeDecodeError:
            pass  # 非文本文件跳过

    passed = len(findings) == 0

    return {
        "passed": passed,
        "score": 1.0 if passed else 0.0,
        "details": "安全扫描通过" if passed else f"发现 {len(findings)} 个问题",
        "findings": findings,
    }


def run_full_evaluation(filepath: str, content: str = "") -> dict:
    """
    执行完整的多角度评估。
    返回 {overall_passed, overall_score, stability, performance, security, recommendation}
    """
    stability = evaluate_stability()
    performance = evaluate_performance()
    security = evaluate_security(filepath, content)

    all_passed = stability["passed"] and performance["passed"] and security["passed"]
    avg_score = (stability["score"] + performance["score"] + security["score"]) / 3

    return {
        "overall_passed": all_passed,
        "overall_score": round(avg_score, 2),
        "stability": stability,
        "performance": performance,
        "security": security,
        "recommendation": "通过" if all_passed else "建议回滚",
    }


# ── 阶段3：自动回滚决策 ─────────────────────────────


def decide_rollback(evaluation: dict, filepath: str) -> tuple[bool, str, str | None]:
    """
    根据评估结果决定是否回滚。

    返回 (should_rollback, reason, backup_id)
    """
    rules = _load_rules()
    cfg = rules["rollback"]

    if not cfg["auto_rollback"]:
        return False, "自动回滚已禁用", None

    if evaluation["overall_passed"]:
        return False, "评估通过，无需回滚", None

    # 找最近的备份
    backups = list_backups(filepath, limit=cfg["max_rollback_depth"])
    if not backups:
        return False, "未找到可用备份", None

    latest = backups[0]
    return True, f"评估未通过(得分{evaluation['overall_score']})，回滚到 {latest['id'][:20]}...", latest["id"]


def execute_rollback_if_needed(evaluation: dict, filepath: str) -> dict:
    """
    评估 + 回滚一步完成。

    返回 {evaluation, rollback_performed, rollback_result, diagnosis}
    """
    should, reason, backup_id = decide_rollback(evaluation, filepath)

    result = {
        "evaluation": evaluation,
        "rollback_performed": False,
        "rollback_result": None,
        "diagnosis": None,
        "reason": reason,
    }

    if should and backup_id:
        restore_result = restore_backup(backup_id)
        result["rollback_performed"] = True
        result["rollback_result"] = restore_result

        # 阶段4：恢复后诊断
        if _load_rules()["rollback"]["diagnose_after_rollback"]:
            result["diagnosis"] = diagnose_after_rollback(filepath)

    _log_evaluation(result)
    return result


# ── 阶段4：恢复后诊断 ────────────────────────────────


def diagnose_after_rollback(filepath: str) -> dict:
    """
    回滚后自我诊断：检查系统是否恢复正常。
    返回 {healthy, checks, summary}
    """
    checks = {}

    # 检查1：文件是否恢复成功
    checks["file_restored"] = os.path.exists(filepath)

    # 检查2：稳定性
    stability = evaluate_stability()
    checks["stability"] = stability["passed"]

    # 检查3：文件语法（如果是 Python 文件）
    if filepath.endswith(".py"):
        try:
            result = subprocess.run(
                ["python3", "-c", f"import py_compile; py_compile.compile('{filepath}', doraise=True)"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            checks["syntax_valid"] = result.returncode == 0
        except subprocess.TimeoutExpired:
            checks["syntax_valid"] = False

    healthy = all(checks.get(k, True) for k in ["file_restored", "stability"])
    checks["healthy"] = healthy
    checks["summary"] = "系统正常" if healthy else "⚠️ 部分检查未通过"

    return checks


# ── 公开 API ────────────────────────────────────────


def get_engine_status() -> dict:
    """获取进化引擎状态。"""
    rules = _load_rules()
    eval_count = 0
    if os.path.exists(EVAL_LOG_PATH):
        with open(EVAL_LOG_PATH) as f:
            eval_count = sum(1 for _ in f)

    return {
        "rules_loaded": len(rules.get("triggers", {}).get("paths", [])),
        "evaluations_logged": eval_count,
        "auto_rollback": rules.get("rollback", {}).get("auto_rollback", False),
    }


def full_evolution_cycle(filepath: str, old_size: int, new_size: int, content: str = "") -> dict:
    """
    完整进化周期：触发判定 → 多角度评估 → 回滚决策 → 执行回滚 → 恢复诊断。

    这是波段二的入口函数。外部只需调用这一个函数。

    返回完整的周期报告。
    """
    cycle = {
        "filepath": filepath,
        "timestamp": datetime.now().isoformat(),
        "stages": {},
    }

    # 阶段1：触发判定
    should, reason = should_trigger_evaluation(
        filepath,
        old_size=old_size,
        new_size=new_size,
    )
    cycle["stages"]["trigger"] = {
        "should_evaluate": should,
        "reason": reason,
    }

    if not should:
        cycle["conclusion"] = "未触发评估"
        _log_evaluation(cycle)
        return cycle

    # 阶段2：多角度评估
    evaluation = run_full_evaluation(filepath, content)
    cycle["stages"]["evaluation"] = evaluation

    # 阶段3-4：回滚决策 + 执行 + 诊断
    rollback_result = execute_rollback_if_needed(evaluation, filepath)
    cycle["stages"]["rollback"] = rollback_result

    # 结论
    if rollback_result["rollback_performed"]:
        cycle["conclusion"] = (
            "已回滚"
            if rollback_result["diagnosis"] and rollback_result["diagnosis"].get("healthy")
            else "回滚后系统异常"
        )
    elif not evaluation["overall_passed"]:
        cycle["conclusion"] = "评估未通过但未回滚"
    else:
        cycle["conclusion"] = "评估通过"

    _log_evaluation(cycle)
    return cycle
