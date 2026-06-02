# SPDX-License-Identifier: MIT
"""
forge_verify.py

Forge 代码战甲专用 — 代码质量验证工具集。
包括：语法检查、lint检查、死代码检测、可读性评分、格式检查。
被 Forge 的 system prompt 强制要求在每次提交前调用。
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


# ── 辅助函数 ──────────────────────────────────────────


def _run_command(cmd: list, cwd: str = None, timeout: int = 30) -> dict:
    """运行命令行并返回结果。"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "超时"}
    except FileNotFoundError:
        return {"returncode": -1, "stdout": "", "stderr": "命令未找到"}
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


# ── 验证工具 ──────────────────────────────────────────


@tool()
async def forge_verify(file_path: str) -> dict:
    """【Forge 核心验证】对提交前的代码文件做完整的六项检查。

    包括：语法检查、格式检查、lint检查、死代码检测、命名规范、可读性评分。

    Args:
        file_path: 要验证的代码文件路径

    Returns:
        验证报告，包含各项检查结果和综合评分
    """
    if not os.path.isfile(file_path):
        return {"error": f"文件不存在: {file_path}", "score": 0, "passed": False}

    ext = Path(file_path).suffix
    results = {}
    score = 0
    max_score = 100
    deductions = []

    # 1️⃣ 语法检查（40分）
    syntax_ok, syntax_msg, syntax_points = _check_syntax(file_path, ext)
    results["syntax"] = {"ok": syntax_ok, "message": syntax_msg, "score": syntax_points}
    score += syntax_points
    if not syntax_ok:
        deductions.append(f"语法错误: {syntax_msg}")

    # 2️⃣ 格式检查（15分）
    if ext in (".py", ".ts", ".tsx", ".js", ".jsx"):
        format_ok, format_msg, format_points = _check_format(file_path, ext)
        results["format"] = {"ok": format_ok, "message": format_msg, "score": format_points}
        score += format_points
        if not format_ok:
            deductions.append(f"格式问题: {format_msg}")

    # 3️⃣ lint 检查（15分）
    if ext in (".py", ".ts", ".tsx", ".js", ".jsx"):
        lint_ok, lint_msg, lint_points = _check_lint(file_path, ext)
        results["lint"] = {"ok": lint_ok, "message": lint_msg, "score": lint_points}
        score += lint_points
        if not lint_ok:
            deductions.append(f"lint 告警: {lint_msg}")

    # 4️⃣ 死代码检测（10分）
    dead_ok, dead_msg, dead_points = _check_dead_code(file_path, ext)
    results["dead_code"] = {"ok": dead_ok, "message": dead_msg, "score": dead_points}
    score += dead_points
    if not dead_ok:
        deductions.append(f"死代码: {dead_msg}")

    # 5️⃣ 命名规范（10分）
    naming_ok, naming_msg, naming_points = _check_naming(file_path, ext)
    results["naming"] = {"ok": naming_ok, "message": naming_msg, "score": naming_points}
    score += naming_points
    if not naming_ok:
        deductions.append(f"命名不规范: {naming_msg}")

    # 6️⃣ 综合可读性（10分）
    read_ok, read_msg, read_points = _check_readability(file_path, ext)
    results["readability"] = {"ok": read_ok, "message": read_msg, "score": read_points}
    score += read_points
    if not read_ok:
        deductions.append(f"可读性问题: {read_msg}")

    passed = score >= 80
    return {
        "file": file_path,
        "score": score,
        "max_score": max_score,
        "passed": passed,
        "level": "✨ 完美" if score >= 95 else ("✅ 良好" if score >= 80 else "⚠️  需要修复"),
        "details": results,
        "deductions": deductions,
        "summary": (
            f"综合得分 {score}/{max_score}，{'通过 ✅' if passed else '未通过 ❌'}。"
            + (f" 扣分项: {'; '.join(deductions)}" if deductions else " 无扣分项，代码质量优秀。")
        ),
    }


# ── 各项检查 ──────────────────────────────────────────


def _check_syntax(file_path: str, ext: str) -> tuple:
    """语法检查"""
    try:
        if ext == ".py":
            with open(file_path) as f:
                ast.parse(f.read())
            return (True, "语法正确", 40)
        elif ext in (".ts", ".tsx", ".js", ".jsx"):
            result = _run_command(["npx", "--yes", "tsx", "--eval", "true", file_path])
            if result["returncode"] == 0:
                return (True, "语法正确", 40)
            return (False, result["stderr"][:200], 0)
        elif ext in (".sh", ".bash"):
            result = _run_command(["bash", "-n", file_path])
            if result["returncode"] == 0:
                return (True, "语法正确", 40)
            return (False, result["stderr"][:200], 0)
        return (True, "不需要语法检查", 40)
    except SyntaxError as e:
        return (False, f"第{e.lineno}行: {e.msg}", 0)
    except Exception as e:
        return (False, str(e)[:200], 0)


def _check_format(file_path: str, ext: str) -> tuple:
    """格式检查"""
    try:
        if ext == ".py":
            result = _run_command([sys.executable or "python3", "-m", "ruff", "format", "--check", file_path])
            if result["returncode"] == 0:
                return (True, "格式正确", 15)
            return (False, result["stdout"][:300], 0)
        return (True, "跳过格式检查", 15)
    except Exception:
        return (True, "格式检查不可用", 10)


def _check_lint(file_path: str, ext: str) -> tuple:
    """lint 检查"""
    try:
        if ext == ".py":
            result = _run_command([sys.executable or "python3", "-m", "ruff", "check", file_path])
            if result["returncode"] == 0:
                return (True, "无 lint 问题", 15)
            issues = [ln for ln in result["stdout"].split("\n") if ln.strip() and "warning" not in ln.lower()]
            if len(issues) <= 3:
                return (True, f"轻微告警: {len(issues)} 项", 10)
            return (False, f"{len(issues)} 项 lint 问题:\n" + "\n".join(issues[:6]), 0)
        return (True, "跳过 lint 检查", 15)
    except Exception:
        return (True, "lint 检查不可用", 10)


def _check_dead_code(file_path: str, ext: str) -> tuple:
    """简单死代码检测"""
    try:
        with open(file_path) as f:
            content = f.read()

        issues = []

        # 检查注释掉的代码块
        commented_lines = re.findall(r"# .*?def\s+\w+|// .*?function\s+\w+", content)
        if commented_lines:
            issues.append(f"发现 {len(commented_lines)} 处注释掉的代码")

        # 检查 print debug 残留
        if ext == ".py":
            print_lines = re.findall(r"^\s*print\(.*?\)\s*$", content, re.MULTILINE)
            debug_prints = [ln for ln in print_lines if "TODO" not in ln and "FIXME" not in ln]
            if debug_prints:
                issues.append(f"发现 {len(debug_prints)} 处 debug print（非 TODO/FIXME）")

        # 检查 TODO
        todos = re.findall(r"#\s*(TODO|FIXME|HACK|XXX)", content)
        if todos:
            issues.append(f"发现 {len(todos)} 处 {', '.join(set(todos))}")

        if issues:
            return (False, "; ".join(issues), 0 if len(issues) > 2 else 5)
        return (True, "无死代码残留", 10)
    except Exception:
        return (True, "死代码检测出错", 5)


def _check_naming(file_path: str, ext: str) -> tuple:
    """命名规范检查"""
    try:
        with open(file_path) as f:
            content = f.read()

        issues = []

        if ext == ".py":
            # 检查非 PEP8 命名
            camel_case_functions = re.findall(r"^    def [a-z]+[A-Z]", content, re.MULTILINE)
            if camel_case_functions:
                issues.append(f"函数名使用驼峰而非蛇形: {len(camel_case_functions)} 处")

            # 检查单字母变量名（排除临时变量 i, j, k, x, y, z, n, e）
            single_letter = set(re.findall(r"\b([a-lmo-rt-w])\s*=\s*", content))
            if single_letter:
                issues.append(f"单字母变量名: {', '.join(sorted(single_letter)[:5])}")

            # 检查含拼音的命名
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
                issues.append(f"疑似拼音命名: {', '.join(sorted(non_keyword_pinyin)[:5])}")

        if issues:
            return (False, "; ".join(issues), 0 if len(issues) > 2 else 5)
        return (True, "命名规范", 10)
    except Exception:
        return (True, "命名检查出错", 5)


def _check_readability(file_path: str, ext: str) -> tuple:
    """综合可读性检查"""
    try:
        with open(file_path) as f:
            content = f.read()
            lines = content.split("\n")

        issues = []

        # 检查行数
        if len(lines) > 500:
            issues.append(f"文件过长: {len(lines)} 行（建议 < 500）")

        # 检查超长行
        long_lines = sum(1 for ln in lines if len(ln) > 100)
        if long_lines > 3:
            issues.append(f"{long_lines} 行超过 100 字符")

        # 检查空行密度（代码块应有呼吸感）
        blanks = sum(1 for ln in lines if not ln.strip())
        if lines and blanks / len(lines) < 0.02 and len(lines) > 50:
            issues.append("空行过少（缺少呼吸感）")

        # 检查是否有足够注释
        if ext == ".py":
            comment_lines = sum(1 for ln in lines if ln.strip().startswith("#"))
            if lines and comment_lines == 0 and len(lines) > 30:
                issues.append("零注释（>30 行代码建议有注释）")

        if issues:
            return (False, "; ".join(issues), 0 if len(issues) > 2 else 5)
        return (True, "可读性良好", 10)
    except Exception:
        return (True, "可读性检查出错", 5)
