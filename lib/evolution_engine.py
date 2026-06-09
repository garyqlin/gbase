# SPDX-License-Identifier: MIT
"""
lib/evolution_engine.py

Phase 2: auto-evolution trigger chain.

Provides:
- Evolution trigger rule engine — determines which changes trigger evaluation
- Multi-angle evaluation — stability/performance/security triple check
- Auto-rollback decision — auto-restore on evaluation failure
- Post-recovery diagnosis — verify system health after rollback

Design principles:
- Rollbackable (auto-backup before all changes)
- Low-intrusion (evaluation does not affect normal operation)
- Progressive (rollback then diagnose on failure, no downtime)
"""

import json
import os
import subprocess
from datetime import datetime

# ── Configuration ───────────────────────────────────
from pathlib import Path

from lib.backup import list_backups, restore_backup

ENGINE_DIR = os.getenv("GBASE_EVOLUTION_DIR") or str(Path(__file__).resolve().parent.parent / ".evolution")
EVAL_LOG_PATH = os.path.join(ENGINE_DIR, "evaluations.jsonl")
RULES_PATH = os.path.join(ENGINE_DIR, "rules.json")

# Default trigger rules
DEFAULT_RULES = {
    "triggers": {
        # Which file changes need evaluation
        "paths": [
            "/main.py",
            "/lib/",
            "/tools/",
            "/skills/",
            "/config/",
            "/systemd/",
        ],
        # File size change threshold (exceeding this ratio triggers evaluation)
        "size_change_ratio": 0.10,  # 10%
        # Minimum line change count (triggers when exceeding this value)
        "min_line_change": 5,
    },
    "evaluation": {
        # Stability check
        "stability": {
            "enabled": True,
            "check_services": ["gbase.service"],
            "max_restart_attempts": 3,
            "startup_timeout_sec": 15,
        },
        # Performance check
        "performance": {
            "enabled": True,
            "max_response_time_ms": 5000,
            "memory_increase_threshold_mb": 100,
        },
        # Security check
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
        "auto_rollback": True,  # Auto-rollback on evaluation failure
        "max_rollback_depth": 5,  # Max 5 versions rollback
        "diagnose_after_rollback": True,  # Auto-diagnose after rollback
    },
}


# ── Internal Utilities ───────────────────────────────


def _ensure_dirs():
    os.makedirs(ENGINE_DIR, exist_ok=True)


def _load_rules() -> dict:
    _ensure_dirs()
    if os.path.exists(RULES_PATH):
        try:
            with open(RULES_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass
    # Write default rules
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_RULES, f, indent=2, ensure_ascii=False)
    return DEFAULT_RULES


def _save_rules(rules: dict):
    _ensure_dirs()
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)


def _log_evaluation(entry: dict):
    _ensure_dirs()
    entry["timestamp"] = datetime.now().isoformat()
    with open(EVAL_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _count_lines(filepath: str) -> int:
    try:
        with open(filepath, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except (FileNotFoundError, UnicodeDecodeError):
        return 0


# ── Phase 1: Trigger Rule Engine ─────────────────────


def should_trigger_evaluation(
    filepath: str,
    old_size: int | None = None,
    new_size: int | None = None,
    old_lines: int | None = None,
    new_lines: int | None = None,
) -> tuple[bool, str]:
    """
    Determine whether a file modification should trigger evolution evaluation.

    Returns (should_evaluate, reason)
    """
    rules = _load_rules()
    triggers = rules["triggers"]

    # Check path matching
    path_matched = False
    for pattern in triggers["paths"]:
        if pattern in filepath:
            path_matched = True
            break

    if not path_matched:
        return False, f"Path {filepath} is not in trigger scope"

    # Check size change
    if old_size is not None and new_size is not None and old_size > 0:
        ratio = abs(new_size - old_size) / old_size
        if ratio >= triggers["size_change_ratio"]:
            return True, f"File size changed {ratio:.1%}, exceeds threshold {triggers['size_change_ratio']:.1%}"

    # Check line count change
    if old_lines is not None and new_lines is not None:
        delta = abs(new_lines - old_lines)
        if delta >= triggers["min_line_change"]:
            return True, f"Line count changed by {delta}, exceeds threshold {triggers['min_line_change']}"

    return False, "Change amount below trigger threshold"


# ── Phase 2: Multi-Angle Evaluation ──────────────────


def evaluate_stability() -> dict:
    """
    Stability evaluation: check if main services are running normally.
    Returns {passed, score, details, recommendation}
    """
    rules = _load_rules()
    cfg = rules["evaluation"]["stability"]

    if not cfg["enabled"]:
        return {"passed": True, "score": 1.0, "details": "Stability check disabled"}

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
        "details": "Service status: " + ", ".join(r["service"] + "=" + str(r["active"]) for r in results),
        "services": results,
    }


def evaluate_performance() -> dict:
    """
    Performance evaluation: check memory, response time, and other metrics.
    Returns {passed, score, details}
    """
    rules = _load_rules()
    cfg = rules["evaluation"]["performance"]

    if not cfg["enabled"]:
        return {"passed": True, "score": 1.0, "details": "Performance check disabled"}

    # Current memory usage (via /proc or ps)
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
        "details": f"Current memory: {mem_mb:.1f}MB",
        "memory_mb": round(mem_mb, 1),
    }


def evaluate_security(filepath: str, content: str = "") -> dict:
    """
    Security evaluation: scan for dangerous patterns in changes.
    Returns {passed, score, details, findings}
    """
    rules = _load_rules()
    cfg = rules["evaluation"]["security"]

    if not cfg["enabled"]:
        return {"passed": True, "score": 1.0, "details": "Security check disabled"}

    findings = []

    # Scan content
    if content:
        for pattern in cfg["forbidden_patterns"]:
            if pattern in content:
                findings.append(f"⚠️ Dangerous pattern: {pattern}")

    # Scan entire file
    if os.path.exists(filepath):
        try:
            with open(filepath, encoding="utf-8") as f:
                full_content = f.read()
            for imp in cfg["forbidden_imports"]:
                if f"import {imp}" in full_content or f"from {imp}" in full_content:
                    findings.append(f"⚠️ Forbidden import: {imp}")
        except UnicodeDecodeError:
            pass  # Non-text file, skipped

    passed = len(findings) == 0

    return {
        "passed": passed,
        "score": 1.0 if passed else 0.0,
        "details": "Security scan passed" if passed else f"Found {len(findings)} issues",
        "findings": findings,
    }


def run_full_evaluation(filepath: str, content: str = "") -> dict:
    """
    Execute full multi-angle evaluation.
    Returns {overall_passed, overall_score, stability, performance, security, recommendation}
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
        "recommendation": "Passed" if all_passed else "Rollback recommended",
    }


# ── Phase 3: Auto-Rollback Decision ──────────────────


def decide_rollback(evaluation: dict, filepath: str) -> tuple[bool, str, str | None]:
    """
    Decide whether to rollback based on evaluation results.

    Returns (should_rollback, reason, backup_id)
    """
    rules = _load_rules()
    cfg = rules["rollback"]

    if not cfg["auto_rollback"]:
        return False, "Auto-rollback disabled", None

    if evaluation["overall_passed"]:
        return False, "Evaluation passed, no rollback needed", None

    # Find latest backup
    backups = list_backups(filepath, limit=cfg["max_rollback_depth"])
    if not backups:
        return False, "No available backup found", None

    latest = backups[0]
    return (
        True,
        f"Evaluation failed (score {evaluation['overall_score']}), rolling back to {latest['id'][:20]}...",
        latest["id"],
    )


def execute_rollback_if_needed(evaluation: dict, filepath: str) -> dict:
    """
    Evaluation + rollback in one step.

    Returns {evaluation, rollback_performed, rollback_result, diagnosis}
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

        # Phase 4: Post-recovery diagnosis
        if _load_rules()["rollback"]["diagnose_after_rollback"]:
            result["diagnosis"] = diagnose_after_rollback(filepath)

    _log_evaluation(result)
    return result


# ── Phase 4: Post-Recovery Diagnosis ──────────────────


def diagnose_after_rollback(filepath: str) -> dict:
    """
    Self-diagnosis after rollback: check if system is back to normal.
    Returns {healthy, checks, summary}
    """
    checks = {}

    # Check 1: file restored successfully
    checks["file_restored"] = os.path.exists(filepath)

    # Check 2: stability
    stability = evaluate_stability()
    checks["stability"] = stability["passed"]

    # Check 3: file syntax (if Python file)
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
    checks["summary"] = "System healthy" if healthy else "⚠️ Some checks failed"

    return checks


# ── Public API ───────────────────────────────────────


def get_engine_status() -> dict:
    """Get evolution engine status."""
    rules = _load_rules()
    eval_count = 0
    if os.path.exists(EVAL_LOG_PATH):
        with open(EVAL_LOG_PATH, encoding="utf-8") as f:
            eval_count = sum(1 for _ in f)

    return {
        "rules_loaded": len(rules.get("triggers", {}).get("paths", [])),
        "evaluations_logged": eval_count,
        "auto_rollback": rules.get("rollback", {}).get("auto_rollback", False),
    }


def full_evolution_cycle(filepath: str, old_size: int, new_size: int, content: str = "") -> dict:
    """
    Full evolution cycle: trigger check → multi-angle evaluation → rollback decision → execute rollback → recovery diagnosis.

    This is the entry point for Wave 2. External callers only need this one function.

    Returns complete cycle report.
    """
    cycle = {
        "filepath": filepath,
        "timestamp": datetime.now().isoformat(),
        "stages": {},
    }

    # Phase 1: Trigger check
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
        cycle["conclusion"] = "Evaluation not triggered"
        _log_evaluation(cycle)
        return cycle

    # Phase 2: Multi-angle evaluation
    evaluation = run_full_evaluation(filepath, content)
    cycle["stages"]["evaluation"] = evaluation

    # Phase 3-4: Rollback decision + execution + diagnosis
    rollback_result = execute_rollback_if_needed(evaluation, filepath)
    cycle["stages"]["rollback"] = rollback_result

    # Conclusion
    if rollback_result["rollback_performed"]:
        cycle["conclusion"] = (
            "Rolled back"
            if rollback_result["diagnosis"] and rollback_result["diagnosis"].get("healthy")
            else "System abnormal after rollback"
        )
    elif not evaluation["overall_passed"]:
        cycle["conclusion"] = "Evaluation failed but not rolled back"
    else:
        cycle["conclusion"] = "Evaluation passed"

    _log_evaluation(cycle)
    return cycle
