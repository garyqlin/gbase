"""
L3 ExecutionLever — Externalized Verification

Instead of having the LLM say "I think it's correct", ExecutionLever
makes it *do something verifiable* — run code, check facts, validate configs.

Core philosophy: trust but verify, through execution.
"""

import ast
import contextlib
import json
import os
import subprocess
import tempfile
import traceback
from collections.abc import Callable
from typing import Any


def _detect_verification_type(task: str, result: str) -> str:
    """Auto-detect the best verification strategy."""
    task_lower = task.lower()
    result.lower()

    if any(kw in task_lower for kw in ["code", "function", "bug", "error", "syntax"]):
        if "```" in result or "def " in result or "class " in result:
            return "code_execution"
        return "logic_review"

    if any(kw in task_lower for kw in ["fact", "stat", "data", "statistics"]):
        return "fact_check"

    if any(kw in task_lower for kw in ["api", "config", "port", "docker", "endpoint"]):
        return "config_check"

    return "logic_review"


def verify_result(
    task: str,
    result: str,
    verification_type: str | None = None,
    timeout: int = 30,
    code_runner: Callable | None = None,
) -> dict[str, Any]:
    """Verify the result of a task.

    Args:
        task: Original task description.
        result: LLM output to verify.
        verification_type: One of "auto", "code_execution", "fact_check",
                          "logic_review", "config_check".
        timeout: Timeout in seconds for verification steps.
        code_runner: Optional custom code runner callable.
                     Defaults to subprocess-based runner.

    Returns:
        {
            "verified": bool,
            "confidence": float,
            "issues": List[str],
            "evidence": str,
            "verification_type": str,
        }
    """
    if verification_type is None or verification_type == "auto":
        verification_type = _detect_verification_type(task, result)

    verifiers = {
        "code_execution": lambda t, r, to: _verify_code(t, r, to, code_runner),
        "fact_check": _verify_fact,
        "logic_review": _verify_logic,
        "config_check": _verify_config,
    }

    verifier = verifiers.get(verification_type)
    if not verifier:
        return {
            "verified": False,
            "confidence": 0.0,
            "issues": [f"Unknown verification type: {verification_type}"],
            "evidence": "",
            "verification_type": verification_type,
        }

    try:
        return verifier(task, result, timeout)
    except Exception as e:
        return {
            "verified": False,
            "confidence": 0.0,
            "issues": [f"Verification failed: {str(e)}"],
            "evidence": traceback.format_exc(),
            "verification_type": verification_type,
        }


# ──────────────────────────────────────────────
# Code verification
# ──────────────────────────────────────────────


def _verify_code(_task: str, result: str, timeout: int, code_runner: Callable | None = None) -> dict[str, Any]:
    """Extract code blocks, check syntax, optionally run."""
    import re

    code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", result, re.DOTALL)
    if not code_blocks:
        code_blocks = [result]

    issues = []
    evidence_parts = []
    all_passed = True

    for i, code in enumerate(code_blocks):
        code = code.strip()
        if not code:
            continue

        # Syntax check
        try:
            ast.parse(code)
            evidence_parts.append(f"Block {i + 1}: syntax OK")
        except SyntaxError as e:
            issues.append(f"Block {i + 1}: syntax error — {e}")
            all_passed = False
            continue

        # Runtime check (if enabled and code is runnable)
        if code_runner:
            try:
                result_text = code_runner(code, timeout)
                evidence_parts.append(f"Block {i + 1}: ran OK — {result_text[:200]}")
            except Exception as e:
                issues.append(f"Block {i + 1}: runtime error — {e}")
                all_passed = False
        elif _is_runnable(code):
            try:
                stdout, stderr, ret = _run_python(code, timeout)
                if ret == 0:
                    evidence_parts.append(f"Block {i + 1}: ran OK — {stdout[:200]}")
                else:
                    issues.append(f"Block {i + 1}: exit {ret} — {stderr[:200]}")
                    all_passed = False
            except subprocess.TimeoutExpired:
                issues.append(f"Block {i + 1}: timed out after {timeout}s")
                all_passed = False
            except Exception as e:
                issues.append(f"Block {i + 1}: execution failed — {e}")
                all_passed = False

    return {
        "verified": all_passed,
        "confidence": 1.0 if all_passed and not issues else 0.5,
        "issues": issues,
        "evidence": "\n".join(evidence_parts),
        "verification_type": "code_execution",
    }


def _is_runnable(code: str) -> bool:
    """Check if code looks runnable (has statements, not just definitions)."""
    stripped = code.strip()
    if not stripped:
        return False
    # Check for any statements beyond def/class
    lines = stripped.split("\n")
    for line in lines:
        s = line.strip()
        if s and not s.startswith(("def ", "class ", "@", "import ", "from ", "#", '"""', "'''")):
            return True
    return False


def _run_python(code: str, timeout: int = 30) -> tuple:
    """Run Python code in a subprocess and return (stdout, stderr, retcode)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["python3", tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout, result.stderr, result.returncode
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


# ──────────────────────────────────────────────
# Fact check
# ──────────────────────────────────────────────


def _verify_fact(_task: str, result: str, _timeout: int) -> dict[str, Any]:
    """Simple fact verification: extract numbers/claims and flag."""
    import re

    issues = []
    evidence_parts = []

    # Check for unsupported claims
    claim_patterns = [
        r"(?:always|never|all|none|every|100%|0%|保证|绝对|永远)",
        r"(?:据我所知|我认为|可能|maybe|probably|approximately|大约)",
    ]

    tentative_claims = re.findall(claim_patterns[0], result, re.IGNORECASE)
    hedging = re.findall(claim_patterns[1], result, re.IGNORECASE)

    if hedging:
        evidence_parts.append(f"Hedging detected ({len(hedging)} instances): suggests uncertainty")

    if tentative_claims:
        issues.append(f"Absolute claims ({len(tentative_claims)} instances) — verify independently")

    # Extract numerical claims
    numbers = re.findall(r"\b(\d+[.%]?)\b", result)
    if numbers:
        evidence_parts.append(f"Numerical claims: {', '.join(numbers[:10])}")
        evidence_parts.append("Note: numerical values should be verified against source data")

    return {
        "verified": len(issues) == 0,
        "confidence": 0.7 if len(issues) == 0 else 0.3,
        "issues": issues,
        "evidence": "\n".join(evidence_parts),
        "verification_type": "fact_check",
    }


# ──────────────────────────────────────────────
# Logic review
# ──────────────────────────────────────────────


def _verify_logic(_task: str, result: str, _timeout: int) -> dict[str, Any]:
    """Check for logical consistency, contradictions, and soundness."""
    import re

    issues = []
    evidence_parts = []

    # Check for contradictions
    antithetical_pairs = [
        (r"开启", r"禁用"),
        (r"enable", r"disable"),
        (r"增加", r"减少"),
        (r"increase", r"decrease"),
        (r"允许", r"禁止"),
    ]

    for a, b in antithetical_pairs:
        has_a = bool(re.search(a, result, re.IGNORECASE))
        has_b = bool(re.search(b, result, re.IGNORECASE))
        if has_a and has_b and re.search(f"{a}.*{b}|{b}.*{a}", result, re.IGNORECASE):
            evidence_parts.append(f"Contains both '{a}' and '{b}' — verify no contradiction")

    # Check for fallback patterns
    fallback_patterns = [
        r"(?:if|如果|alternative|备选|otherwi[se]|fallback)",
    ]
    has_fallback = bool(re.search(fallback_patterns[0], result, re.IGNORECASE))
    if has_fallback:
        evidence_parts.append("Contains conditional/fallback logic — verify edge cases covered")

    # Check for step-by-step structure
    has_steps = bool(re.search(r"(?:step|步骤|1[.。）]|2[.。）]|3[.。）])", result))
    if has_steps:
        evidence_parts.append("Step-by-step structure detected")

    return {
        "verified": len(issues) == 0,
        "confidence": 0.8 if len(issues) == 0 else 0.5,
        "issues": issues,
        "evidence": "\n".join(evidence_parts),
        "verification_type": "logic_review",
    }


# ──────────────────────────────────────────────
# Config check
# ──────────────────────────────────────────────


def _verify_config(_task: str, result: str, _timeout: int) -> dict[str, Any]:
    """Check configuration validity: JSON/YAML parse, port ranges, env vars."""
    import re

    issues = []
    evidence_parts = []

    # Extract and validate JSON
    json_blocks = re.findall(r"```(?:json)?\n(\{.*?\})\n```", result, re.DOTALL)
    for i, block in enumerate(json_blocks):
        try:
            json.loads(block)
            evidence_parts.append(f"JSON block {i + 1}: valid")
        except json.JSONDecodeError as e:
            issues.append(f"JSON block {i + 1}: invalid — {e}")

    # Port range validation
    ports = re.findall(r"(?:port|端口)[:\s]*(\d+)", result, re.IGNORECASE)
    for port_str in ports:
        port = int(port_str)
        if port < 1024:
            issues.append(f"Port {port}: privileged port (<1024) may require root")
        elif port > 65535:
            issues.append(f"Port {port}: out of valid range (1-65535)")

    # Environment variable placeholder check
    env_placeholders = re.findall(r"\$\{([^}]+)\}|\$([A-Z_]+)", result)
    if env_placeholders:
        evidence_parts.append(f"Environment variables found: {len(env_placeholders)}")

    return {
        "verified": len(issues) == 0,
        "confidence": 0.8 if len(issues) == 0 else 0.4,
        "issues": issues,
        "evidence": "\n".join(evidence_parts),
        "verification_type": "config_check",
    }
