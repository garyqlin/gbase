# SPDX-License-Identifier: MIT
"""
lifeline.py — Opprime 自救系统

三层保护架构：
  快照层 (SNAPSHOT)  → 每次进化前自动 Git 提交，打标签
  回滚层 (ROLLBACK)  → 一键回滚到任意快照
  熔断层 (FUSEBREAK) → 检测异常状态自动触发回滚

用法：
    python3 -m lib.lifeline snapshot     # 手动打快照
    python3 -m lib.lifeline list         # 查看快照列表
    python3 -m lib.lifeline rollback TAG # 回滚到指定快照
    python3 -m lib.lifeline check        # 健康检查
    python3 -m lib.lifeline edit <文件路径> <原因>  # 改代码前打快照
"""

import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 配置 ──
ROOT = Path(__file__).resolve().parent.parent
GIT_DIR = ROOT / ".git"
SNAPSHOT_LOG = ROOT / "data" / "snapshots.json"
BACKUP_DIR = ROOT / ".backups"
MAX_BACKUPS = 10


# ── 快照系统 ──


def git_available() -> bool:
    return GIT_DIR.exists()


def get_current_commit() -> str:
    if not git_available():
        return "NO_GIT"
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def get_current_branch() -> str:
    if not git_available():
        return "NO_GIT"
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip() if r.returncode == 0 else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def take_snapshot(reason: str = "") -> dict:
    """创建快照：Git 提交 + 自动标签 + 目录备份"""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = f"snap-{timestamp}"
    results = {
        "tag": tag,
        "timestamp": datetime.now().isoformat(),
        "reason": reason,
        "commit": "",
        "git_ok": False,
        "backup_ok": False,
    }

    # 1. Git 快照
    if git_available():
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=5
            )
            has_changes = bool(status.stdout.strip())
            if has_changes:
                subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True, timeout=10)
                subprocess.run(
                    ["git", "commit", "-m", f"snapshot: {reason or '自动快照'}", "--allow-empty"],
                    cwd=ROOT,
                    capture_output=True,
                    timeout=10,
                )
            subprocess.run(
                ["git", "tag", "-f", tag, "-m", reason or "自动快照"], cwd=ROOT, capture_output=True, timeout=5
            )
            results["commit"] = get_current_commit()
            results["git_ok"] = True
            logger.info("✅ Git 快照 %s (commit: %s)", tag, results["commit"])
        except Exception as e:
            logger.error("❌ Git 快照失败: %s", e)

    # 2. 目录备份
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = BACKUP_DIR / f"{tag}.tar.gz"
        excludes = [
            "--exclude=.git",
            "--exclude=__pycache__",
            "--exclude=*.pyc",
            "--exclude=data/dat.db*",
            "--exclude=data/sessions",
            "--exclude=data/traces",
            "--exclude=.backups",
            "--exclude=*.log",
            "--exclude=nohup.out",
        ]
        subprocess.run(
            ["tar", "czf", str(backup_path)] + excludes + ["-C", str(ROOT), "."], capture_output=True, timeout=30
        )
        results["backup_ok"] = True
        results["backup_path"] = str(backup_path)
        logger.info("✅ 目录备份 %s", tag)
        _cleanup_old_backups()
    except Exception as e:
        logger.error("❌ 目录备份失败: %s", e)

    # 3. 记录日志
    _log_snapshot(results)
    return results


def _log_snapshot(result: dict):
    try:
        SNAPSHOT_LOG.parent.mkdir(parents=True, exist_ok=True)
        snapshots = json.loads(SNAPSHOT_LOG.read_text()) if SNAPSHOT_LOG.exists() else []
        snapshots.append(result)
        if len(snapshots) > 100:
            snapshots = snapshots[-100:]
        SNAPSHOT_LOG.write_text(json.dumps(snapshots, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.error("❌ 无法写入快照日志: %s", e)


def _cleanup_old_backups():
    try:
        backups = sorted(BACKUP_DIR.glob("snap-*.tar.gz"))
        while len(backups) > MAX_BACKUPS:
            backups.pop(0).unlink()
    except Exception:
        pass


# ── 快照列表 ──


def list_snapshots(limit: int = 20) -> list:
    if not SNAPSHOT_LOG.exists():
        return []
    try:
        snapshots = json.loads(SNAPSHOT_LOG.read_text())
        return snapshots[-limit:]
    except Exception:
        return []


def list_git_tags() -> list:
    if not git_available():
        return []
    try:
        r = subprocess.run(
            ["git", "tag", "-l", "snap-*", "--sort=-creatordate"], cwd=ROOT, capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip().split("\n") if r.stdout.strip() else []
    except Exception:
        return []


# ── 回滚系统 ──


def rollback_to(tag: str) -> dict:
    """回滚到指定快照。优先 Git，其次目录备份。"""
    result = {"tag": tag, "success": False, "method": "", "message": ""}

    # 方式一：Git 回滚
    if git_available():
        try:
            r = subprocess.run(["git", "tag", "-l", tag], cwd=ROOT, capture_output=True, text=True, timeout=5)
            if tag in r.stdout:
                pre = take_snapshot(reason=f"pre-rollback-to-{tag}")
                subprocess.run(["git", "checkout", "--force", tag], cwd=ROOT, capture_output=True, timeout=10)
                result["success"] = True
                result["method"] = "git"
                result["message"] = f"已回滚到 Git 标签 {tag}"
                result["pre_rollback_tag"] = pre.get("tag", "")
                return result
        except Exception as e:
            result["message"] = f"Git 回滚失败: {e}，尝试目录备份..."

    # 方式二：目录备份
    backup_file = BACKUP_DIR / f"{tag}.tar.gz"
    if backup_file.exists():
        try:
            pre = take_snapshot(reason=f"pre-rollback-to-{tag}")
            subprocess.run(["tar", "xzf", str(backup_file), "-C", str(ROOT)], capture_output=True, timeout=30)
            result["success"] = True
            result["method"] = "backup"
            result["message"] = f"已从目录备份 {tag}.tar.gz 恢复"
            result["pre_rollback_tag"] = pre.get("tag", "")
            return result
        except Exception as e:
            result["message"] = f"目录备份回滚失败: {e}"

    result["message"] = f"未找到快照 {tag}"
    return result


def rollback_latest() -> dict:
    snapshots = list_snapshots(limit=2)
    if len(snapshots) < 2:
        return {"success": False, "message": "没有可回滚的上一个快照"}
    return rollback_to(snapshots[-2]["tag"])


# ── 健康检查 ──


def health_check() -> dict:
    issues, suggestions = [], []

    # Git 状态
    if git_available():
        try:
            r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=5)
            uncommitted = r.stdout.strip()
            if uncommitted:
                n = len(uncommitted.split("\n"))
                if n > 5:
                    issues.append(f"有 {n} 个文件未提交")
                    suggestions.append("执行 'python3 -m lib.lifeline snapshot' 提交")
        except Exception:
            issues.append("Git 状态检查失败")
    else:
        issues.append("Git 仓库不可用")

    # main.py 语法
    main_py = ROOT / "main.py"
    if main_py.exists():
        try:
            r = subprocess.run(
                [sys.executable, "-m", "py_compile", str(main_py)], capture_output=True, text=True, timeout=5
            )
            if r.returncode != 0:
                issues.append(f"main.py 语法错误: {r.stderr.strip()[:100]}")
                suggestions.append("立即回滚: 'python3 -m lib.lifeline rollback_latest'")
        except Exception:
            pass

    # 磁盘空间
    try:
        stat = shutil.disk_usage(ROOT)
        free_gb = stat.free / (1024**3)
        if free_gb < 0.5:
            issues.append(f"磁盘空间不足: {free_gb:.1f}GB")
            suggestions.append("清理日志和旧备份")
    except Exception:
        pass

    # 关键文件
    for f in ["main.py", "lib/kernel.py", "lib/identity.py", "lib/skill_loader.py", "lib/toolkit.py"]:
        if not (ROOT / f).exists():
            issues.append(f"关键文件缺失: {f}")
            suggestions.append("立即回滚到最近的完整快照")

    # 回滚频率检测
    snapshots = list_snapshots(limit=3)
    rb_count = sum(1 for s in snapshots if "rollback" in s.get("reason", ""))
    if rb_count >= 2:
        issues.append(f"最近3次快照中有 {rb_count} 次回滚")
        suggestions.append("建议暂停自动进化，检查根因")

    return {
        "healthy": len(issues) == 0,
        "issues": issues,
        "suggestions": suggestions,
        "commit": get_current_commit(),
        "branch": get_current_branch(),
        "snapshot_count": len(list_snapshots()),
        "timestamp": datetime.now().isoformat(),
    }


# ── 进化前检查 ──


def pre_evolution_check() -> dict:
    warnings = []
    snapshots = list_snapshots(limit=1)
    if snapshots:
        hours_since = (datetime.now() - datetime.fromisoformat(snapshots[0]["timestamp"])).total_seconds() / 3600
        if hours_since > 24:
            warnings.append(f"上次快照已是 {hours_since:.0f} 小时前")
    else:
        warnings.append("从未创建过快照")
    health = health_check()
    if not health["healthy"]:
        warnings.extend(health["issues"])
    return {"pass": len(warnings) == 0, "warnings": warnings, "should_snapshot_first": len(snapshots) == 0}


# ── 改代码前自动打快照 ──

CODE_PATTERNS = [
    "main.py",
    "lib/",
    "skills/",
    "tools/",
    ".py",
    ".sh",
    ".yaml",
    ".yml",
    ".json",
    ".md",
]


def is_code_file(filepath: str) -> bool:
    """判断一个文件路径是否属于代码/配置类文件"""
    fp = filepath.replace("\\", "/")
    return any(pat in fp for pat in CODE_PATTERNS)


def snapshot_before_edit(filepath: str, reason: str = "") -> dict:
    """
    改代码之前调这个函数。
    它会自动判断文件类型，如果是代码/配置文件就自动打快照。

    用法：
        result = snapshot_before_edit("main.py", "优化记忆模块")
        if result["snapshot_taken"]:
            print(f"已打快照: {result['tag']}")
        # 然后放心改代码
    """
    if not is_code_file(filepath):
        return {
            "snapshot_taken": False,
            "reason": f"{filepath} 不是代码文件，跳过快照",
            "tag": "",
        }

    reason_text = f"改代码前: {reason or filepath}"
    result = take_snapshot(reason=reason_text)

    return {
        "snapshot_taken": True,
        "tag": result["tag"],
        "git_ok": result["git_ok"],
        "backup_ok": result["backup_ok"],
        "reason": reason_text,
        "timestamp": result["timestamp"],
    }


# ── CLI 入口 ──


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if len(sys.argv) < 2:
        print("用法: python3 -m lib.lifeline <命令>")
        print("命令:")
        print("  snapshot [原因]     — 手动打快照")
        print("  list [数量]         — 查看快照列表")
        print("  rollback <标签>     — 回滚到指定快照")
        print("  rollback-latest     — 回滚到上一个快照")
        print("  check               — 健康检查")
        print("  tags                — 列出 Git 标签")
        print("  edit <路径> [原因]  — 改代码前打快照")
        return

    cmd = sys.argv[1]
    if cmd == "snapshot":
        reason = sys.argv[2] if len(sys.argv) > 2 else "手动快照"
        print(json.dumps(take_snapshot(reason), indent=2, ensure_ascii=False))
    elif cmd == "list":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        snapshots = list_snapshots(limit)
        if not snapshots:
            print("暂无快照记录")
        else:
            print(f"{'标签':<25} {'时间':<25} {'原因':<30} {'Git':<10} {'备份':<10}")
            print("-" * 100)
            for s in reversed(snapshots):
                print(
                    f"{s['tag']:<25} {s.get('timestamp', '')[:19]:<25} "
                    f"{s.get('reason', '')[:28]:<30} "
                    f"{'✅' if s.get('git_ok') else '❌':<10} "
                    f"{'✅' if s.get('backup_ok') else '❌':<10}"
                )
    elif cmd == "rollback":
        if len(sys.argv) < 3:
            print("请指定要回滚到的标签")
            return
        print(json.dumps(rollback_to(sys.argv[2]), indent=2, ensure_ascii=False))
    elif cmd == "rollback-latest":
        print(json.dumps(rollback_latest(), indent=2, ensure_ascii=False))
    elif cmd == "check":
        h = health_check()
        if h["healthy"]:
            print("✅ 系统健康")
        else:
            print("⚠️  发现以下问题:")
            for i in h["issues"]:
                print(f"  ❌ {i}")
            if h["suggestions"]:
                print("\n建议:")
                for s in h["suggestions"]:
                    print(f"  💡 {s}")
        print(f"\n  分支: {h['branch']}\n  提交: {h['commit']}\n  快照数: {h['snapshot_count']}")
    elif cmd == "tags":
        for t in list_git_tags():
            print(t)
    elif cmd == "edit":
        if len(sys.argv) < 3:
            print("用法: python3 -m lib.lifeline edit <文件路径> [原因]")
            return
        filepath = sys.argv[2]
        reason = sys.argv[3] if len(sys.argv) > 3 else ""
        print(json.dumps(snapshot_before_edit(filepath, reason), indent=2, ensure_ascii=False))
    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
