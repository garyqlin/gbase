# SPDX-License-Identifier: MIT
"""
沙箱安全推演 — Gbase 版本共享的盲执行防火墙。

核心机制：修改核心文件或调用外部接口前，强制做三步推演。

适用版本：所有 --edition 启动的实例。
接入方式：main.py 启动时 _setup() 中初始化一次，修改核心文件时由 self_mod.py 或 lint 流程调用。
"""

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("sandbox")

# ── 需要推演的文件通配 ──
CRITICAL_FILES = [
    "main.py",
    "lib/kernel.py",
    "lib/session.py",
    "lib/toolkit.py",
    "lib/mirror.py",
    "lib/scheduler.py",
    "lib/auto_learn.py",
    "lib/channels/feishu.py",
    "editions/__init__.py",
    "tools/__init__.py",
    "data/rules/failure-patterns.md",
]

CRITICAL_PATTERNS = [
    re.compile(r)
    for r in [
        r"def feishu_mode\b",
        r"def cli_mode\b",
        r"async def kernel\.run\b",
        r"import.*from lib\.(mirror|kernel|session|toolkit)",
        r"uvicorn\.run|uvicorn\.Config",
        r"FastAPI\(\)",
        r"@app\.(post|get|put|delete)\(/feishu/",
        r"FeishuChannel\(",
        r"set_global\(",
        r"if\s+edition\.modules",
        r"MOD_RSI|MOD_COGNIFOLD|MOD_DAG|MOD_PORTAL",
        r"add_job\(",
        r"AutoLearner\(",
    ]
]


def is_critical(file_path: str) -> bool:
    """判断文件是否属于关键文件，需要走沙箱推演。"""
    rel: str = (
        str(Path(file_path).relative_to(Path(__file__).parent.parent))
        if str(file_path).startswith(str(Path(__file__).parent.parent))
        else file_path
    )
    for critical in CRITICAL_FILES:
        if rel.endswith(critical):
            return True
    return False


def has_critical_change(content: str) -> bool:
    """检查变更内容是否包含关键模式。"""
    for pattern in CRITICAL_PATTERNS:
        if pattern.search(content):
            return True
    return False


def check_failure_patterns(file_path: str, new_content: str) -> list[str]:
    """
    在修改前检查新内容是否命中失败模式。

    返回匹配的 FP 列表，空列表表示无问题。
    """
    hits: list[str] = []

    # FP-001：盲执行（先确认文件是不是新创建的，旧文件看 diff）
    if os.path.exists(file_path):
        try:
            old = Path(file_path).read_text(encoding="utf-8")
            if old == new_content:
                hits.append("FP-001: 新内容和旧内容完全一致，确认需要的修改。")
        except Exception:
            pass
    else:
        # 新文件：确保知道在做什么
        if not new_content.strip():
            hits.append("FP-001: 新文件内容为空，确认这是预期行为？")

    # FP-002：硬编码路径 / ID 冲突
    if "8420" in new_content and "8425" in new_content:
        matches = re.findall(r"port\s*[=:]\s*(8420|8425|8426|8427)", new_content)
        if len(matches) > 1 and len(set(matches)) > 1:
            hits.append(f"FP-002: 文件内含多版本端口 (发现 {set(matches)})，确认不该拆到对应 port 文件")
    port_dups = re.findall(r"port[=:]\s*(\d{4})", new_content)
    if port_dups and len(port_dups) != len(set(port_dups)):
        hits.append(f"FP-002: 重复端口 ({port_dups}) — ID 碰撞风险")

    # FP-003：条件分支非穷举
    conditional_count = new_content.count("if edition") + new_content.count("if MOD_")
    else_count = new_content.count("else")
    if conditional_count > else_count * 2:
        hits.append(f"FP-003: {conditional_count} 个版本条件分支，只有 {else_count} 个 else，有非穷举分支风险")

    # FP-004：未验证的 import/依赖
    import_tk = re.findall(r"^from lib\.(\w+) import", new_content, re.MULTILINE)
    for lib in import_tk:
        lib_path = Path(__file__).parent / f"{lib}.py"
        if not lib_path.exists():
            hits.append(f"FP-004: from lib.{lib} 可能不存在 ({lib_path} not found)")

    # FP-005：改前没备份
    if "lifeline" not in new_content and any(kw in new_content for kw in ["port=", "api_key=", "secret="]):
        # 改端口/API key 建议加备份
        pass

    return hits


def run_sandbox(file_path: str, new_content: str) -> dict:
    """
    沙箱推演主入口。

    1. 重读代码 → 确定文件内容和上下文
    2. 脑内 trace → 跑一遍变更后的逻辑路径（版本条件、初始化顺序）
    3. 检查 FP → 命中失败模式则告警

    返回: {
        "pass": bool,
        "warnings": [str],
        "fps_hit": [str],
        "recommended": str (建议操作)
    }
    """
    warnings: list[str] = []
    fps: list[str] = []

    # Step 1: 检查是否是关键文件
    if not is_critical(file_path):
        return {"pass": True, "warnings": [], "fps_hit": [], "recommended": "非关键文件，无需推演"}

    # Step 2: 检查变更内容是否有关键模式
    if not has_critical_change(new_content):
        return {
            "pass": True,
            "warnings": ["文件属关键列表但内容不涉及关键模式，可放心修改"],
            "fps_hit": [],
            "recommended": "通过",
        }

    # Step 3: 逐条 FP 检查
    fps = check_failure_patterns(file_path, new_content)

    if fps:
        for fp in fps:
            logger.warning("  🚫 %s", fp)
            warnings.append(fp)
        return {
            "pass": False,
            "warnings": warnings,
            "fps_hit": fps,
            "recommended": "停止修改。逐条处理以上 FP 告警后再执行。",
        }

    # Step 4: 脑内 trace（简化版：识别关键路径）
    critical_paths = []
    if "feishu_mode" in new_content:
        critical_paths.append("feishu_mode 修改 → 检查调度器初始化链")
    if "cli_mode" in new_content:
        critical_paths.append("cli_mode 修改 → 检查会话循环")
    if "Kernel(" in new_content:
        critical_paths.append("Kernel 初始化 → 检查参数一致性")
    if "scheduler" in new_content:
        critical_paths.append("调度器修改 → 检查 add_job/run 路径")
    if "auto_learner" in new_content:
        critical_paths.append("自主学习修改 → 检查 sender_func/kernel_run_func")
    if "edition" in new_content:
        critical_paths.append("版本门控修改 → 逐版本检查 else/fallback")

    if critical_paths:
        warnings.append("脑内 trace 关键路径:")
        for p in critical_paths:
            warnings.append(f"  → {p}")

    return {
        "pass": True,
        "warnings": warnings,
        "fps_hit": [],
        "recommended": "✅ 通过。注意脑内 trace 路径后执行。",
    }


def lint(file_path: str) -> list[str]:
    """文件级的版本兼容性扫描（新写代码时的 lint 检查）。"""
    issues: list[str] = []
    content = Path(file_path).read_text(encoding="utf-8")
    lines = content.splitlines()

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # 检查硬编码端口
        ports = re.findall(r"\b(842[0-9]|843[0-9])\b", stripped)
        if ports and not stripped.startswith("#") and "port" not in stripped:
            issues.append(f"Line {i}: 硬编码端口 {ports} — 应通过 edition.modules 读取")

        # 检查无条件导入已废弃模块
        deprecations = {
            "from lib.cognifold": "仅极客版/旗舰版加载，需包装在 MOD_COGNIFOLD 检查中",
        }
        for dep, note in deprecations.items():
            if dep in stripped and not stripped.startswith("#"):
                issues.append(f"Line {i}: '{dep}' — {note}")

    return issues


# ── 如果直接运行，对指定文件做推演 ──

if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else ""
    if target:
        print(f"🔍 沙箱推演: {target}")
        try:
            content = Path(target).read_text(encoding="utf-8")
            result = run_sandbox(target, content)
            if result["pass"]:
                print("✅ 推演通过")
            else:
                print("❌ 推演未通过")
            for w in result["warnings"]:
                print(f"  ⚠ {w}")
        except FileNotFoundError:
            print(f"❌ 文件不存在: {target}")
    else:
        print("用法: python3 lib/sandbox_safety.py <文件路径>")
