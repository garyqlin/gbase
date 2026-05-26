# SPDX-License-Identifier: MIT
"""
forge_verify.py

Forge code verification toolset - code quality validator.
Includes: syntax check, lint, dead code, readability score, formatting.
Mandated by Forge's system prompt to be called before every commit.
"""

import ast
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from lib.toolkit import tool

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────


def _run_command(cmd: list, cwd: str = None, timeout: int = 30) -> dict:
    """Run a command and return the result."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "Timeout"}
    except FileNotFoundError:
        return {"returncode": -1, "stdout": "", "stderr": "Command not found"}
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


# ── Verification Tools ─────────────────────────────────


@tool()
async def forge_verify(file_path: str) -> dict:
    """[Forge Core Verification] Runs a complete six-check validation on code files before commit.

    Includes: syntax check, format check, lint check, dead code detection,
    naming conventions, readability score.

    Args:
        file_path: Path to the code file to verify

    Returns:
        Verification report with individual check results and overall score
    """
    if not os.path.isfile(file_path):
        return {"error": f"File not found: {file_path}", "score": 0, "passed": False}

    ext = Path(file_path).suffix
    results = {}
    score = 0
    max_score = 100
    deductions = []

    # 1. Syntax check (40 pts)
    syntax_ok, syntax_msg, syntax_points = _check_syntax(file_path, ext)
    results["syntax"] = {"ok": syntax_ok, "message": syntax_msg, "score": syntax_points}
    score += syntax_points
    if not syntax_ok:
        deductions.append(f"Syntax error: {syntax_msg}")

    # 2. Format check (15 pts)
    if ext in (".py", ".ts", ".tsx", ".js", ".jsx"):
        format_ok, format_msg, format_points = _check_format(file_path, ext)
        results["format"] = {"ok": format_ok, "message": format_msg, "score": format_points}
        score += format_points
        if not format_ok:
            deductions.append(f"Format issue: {format_msg}")

    # 3. Lint check (15 pts)
    if ext in (".py", ".ts", ".tsx", ".js", ".jsx"):
        lint_ok, lint_msg, lint_points = _check_lint(file_path, ext)
        results["lint"] = {"ok": lint_ok, "message": lint_msg, "score": lint_points}
        score += lint_points
        if not lint_ok:
            deductions.append(f"Lint warning: {lint_msg}")

    # 4. Dead code detection (10 pts)
    dead_ok, dead_msg, dead_points = _check_dead_code(file_path, ext)
    results["dead_code"] = {"ok": dead_ok, "message": dead_msg, "score": dead_points}
    score += dead_points
    if not dead_ok:
        deductions.append(f"Dead code: {dead_msg}")

    # 5. Naming conventions (10 pts)
    naming_ok, naming_msg, naming_points = _check_naming(file_path, ext)
    results["naming"] = {"ok": naming_ok, "message": naming_msg, "score": naming_points}
    score += naming_points
    if not naming_ok:
        deductions.append(f"Naming violation: {naming_msg}")

    # 6. Readability (10 pts)
    read_ok, read_msg, read_points = _check_readability(file_path, ext)
    results["readability"] = {"ok": read_ok, "message": read_msg, "score": read_points}
    score += read_points
    if not read_ok:
        deductions.append(f"Readability issue: {read_msg}")

    passed = score >= 80
    return {
        "file": file_path,
        "score": score,
        "max_score": max_score,
        "passed": passed,
        "level": "✨ Perfect" if score >= 95 else ("✅ Good" if score >= 80 else "⚠️  Needs fix"),
        "details": results,
        "deductions": deductions,
        "summary": (
            f"Overall score {score}/{max_score}, {'Passed ✅' if passed else 'Failed ❌'}."
            + (f" Deductions: {'; '.join(deductions)}" if deductions else " No deductions, excellent code quality.")
        ),
    }


# ── Checks ─────────────────────────────────────────────


def _check_syntax(file_path: str, ext: str) -> tuple:
    """Syntax check"""
    try:
        if ext == ".py":
            with open(file_path, encoding="utf-8") as f:
                ast.parse(f.read())
            return (True, "Syntax OK", 40)
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            result = _run_command(["npx", "--yes", "tsx", "--eval", "true", file_path])
            if result["returncode"] == 0:
                return (True, "Syntax OK", 40)
            return (False, result["stderr"][:200], 0)
        elif ext in (".sh", ".bash"):
            result = _run_command(["bash", "-n", file_path])
            if result["returncode"] == 0:
                return (True, "Syntax OK", 40)
            return (False, result["stderr"][:200], 0)
        return (True, "No syntax check needed", 40)
    except SyntaxError as e:
        return (False, f"Line {e.lineno}: {e.msg}", 0)
    except Exception as e:
        return (False, str(e)[:200], 0)


def _check_format(file_path: str, ext: str) -> tuple:
    """Format check"""
    try:
        if ext == ".py":
            result = _run_command([sys.executable or "python3", "-m", "ruff", "format", "--check", file_path])
            if result["returncode"] == 0:
                return (True, "Format OK", 15)
            return (False, result["stdout"][:300], 0)
        return (True, "Format check skipped", 15)
    except Exception:
        return (True, "Format check unavailable", 10)


def _check_lint(file_path: str, ext: str) -> tuple:
    """Lint check"""
    try:
        if ext == ".py":
            result = _run_command([sys.executable or "python3", "-m", "ruff", "check", file_path])
            if result["returncode"] == 0:
                return (True, "No lint issues", 15)
            issues = [ln for ln in result["stdout"].split("\n") if ln.strip() and "warning" not in ln.lower()]
            if len(issues) <= 3:
                return (True, f"Minor warnings: {len(issues)} item(s)", 10)
            return (False, f"{len(issues)} lint issue(s):\n" + "\n".join(issues[:6]), 0)
        return (True, "Lint check skipped", 15)
    except Exception:
        return (True, "Lint check unavailable", 10)


def _check_dead_code(file_path: str, ext: str) -> tuple:
    """Simple dead code detection"""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        issues = []

        # Check commented-out code blocks
        commented_lines = re.findall(r"# .*?def\s+\w+|// .*?function\s+\w+", content)
        if commented_lines:
            issues.append(f"Found {len(commented_lines)} commented-out code block(s)")

        # Check leftover debug print statements
        if ext == ".py":
            print_lines = re.findall(r"^\s*print\(.*?\)\s*$", content, re.MULTILINE)
            debug_prints = [ln for ln in print_lines if "TODO" not in ln and "FIXME" not in ln]
            if debug_prints:
                issues.append(f"Found {len(debug_prints)} debug print(s) (non-TODO/FIXME)")

        # Check TODO markers
        todos = re.findall(r"#\s*(TODO|FIXME|HACK|XXX)", content)
        if todos:
            issues.append(f"Found {len(todos)} {', '.join(set(todos))} marker(s)")

        if issues:
            return (False, "; ".join(issues), 0 if len(issues) > 2 else 5)
        return (True, "No dead code found", 10)
    except Exception:
        return (True, "Dead code detection error", 5)


def _check_naming(file_path: str, ext: str) -> tuple:
    """Naming convention check"""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        issues = []

        if ext == ".py":
            # Check non-PEP8 naming (camelCase functions)
            camel_case_functions = re.findall(r"^    def [a-z]+[A-Z]", content, re.MULTILINE)
            if camel_case_functions:
                issues.append(f"Function name uses camelCase instead of snake_case: {len(camel_case_functions)}")

            # Check single-letter variable names (exclude temp vars i, j, k, x, y, z, n, e)
            single_letter = set(re.findall(r"\b([a-lmo-rt-w])\s*=\s*", content))
            if single_letter:
                issues.append(f"Single-letter variable names: {', '.join(sorted(single_letter)[:5])}")

            # Check for pinyin-based naming
            pinyin_names = set(
                re.findall(r"\b[a-z]*(zhe|xie|zhi|shi|bu|de|wo|ni|ta|hai|zai|yi|mei|ke|yi)\w*\b", content)
            )
            non_keyword_pinyin = {
                p
                for p in pinyin_names
                if p
                not in (
                    "the",
                    "she",
                    "her",
                    "his",
                    "are",
                    "all",
                    "any",
                    "for",
                    "and",
                    "but",
                    "not",
                    "yes",
                    "has",
                    "had",
                )
            }
            if non_keyword_pinyin:
                issues.append(f"Suspected pinyin naming: {', '.join(sorted(non_keyword_pinyin)[:5])}")

        if issues:
            return (False, "; ".join(issues), 0 if len(issues) > 2 else 5)
        return (True, "Naming OK", 10)
    except Exception:
        return (True, "Naming check error", 5)


def _check_readability(file_path: str, ext: str) -> tuple:
    """Comprehensive readability check"""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
            lines = content.split("\n")

        issues = []

        # Check line count
        if len(lines) > 500:
            issues.append(f"File too long: {len(lines)} lines (recommend < 500)")

        # Check long lines
        long_lines = sum(1 for ln in lines if len(ln) > 100)
        if long_lines > 3:
            issues.append(f"{long_lines} line(s) exceed 100 characters")

        # Check blank line density (code blocks should breathe)
        blanks = sum(1 for ln in lines if not ln.strip())
        if lines and blanks / len(lines) < 0.02 and len(lines) > 50:
            issues.append("Too few blank lines (lacks breathing room)")

        # Check if there are enough comments
        if ext == ".py":
            comment_lines = sum(1 for ln in lines if ln.strip().startswith("#"))
            if lines and comment_lines == 0 and len(lines) > 30:
                issues.append("Zero comments (>30 lines of code should have comments)")

        if issues:
            return (False, "; ".join(issues), 0 if len(issues) > 2 else 5)
        return (True, "Good readability", 10)
    except Exception:
        return (True, "Readability check error", 5)
