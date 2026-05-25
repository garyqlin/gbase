# SPDX-License-Identifier: MIT
"""
lifeline.py — Self-preservation system

Three-layer protection architecture:
  Snapshot layer (SNAPSHOT)  → Auto Git commit + tag before each evolution
  Rollback layer (ROLLBACK)  → One-click rollback to any snapshot
  Fusebreak layer (FUSEBREAK) → Detect abnormal state and auto-trigger rollback

Usage:
    python3 -m lib.lifeline snapshot            # Manually take a snapshot
    python3 -m lib.lifeline list                # View snapshot list
    python3 -m lib.lifeline rollback TAG        # Rollback to a specified snapshot
    python3 -m lib.lifeline check               # Health check
    python3 -m lib.lifeline edit <filepath> <reason>  # Take snapshot before editing code
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Config ──
ROOT = Path(os.getenv("GBASE_ROOT_DIR", "."))
GIT_DIR = ROOT / ".git"
SNAPSHOT_LOG = ROOT / "data" / "snapshots.json"
BACKUP_DIR = ROOT / ".backups"
MAX_BACKUPS = 10


# ── Snapshot System ──


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
    """Create snapshot: Git commit + auto tag + directory backup"""
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

    # 1. Git snapshot
    if git_available():
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=5
            )
            has_changes = bool(status.stdout.strip())
            if has_changes:
                subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True, timeout=10)
                subprocess.run(
                    ["git", "commit", "-m", f"snapshot: {reason or 'auto snapshot'}", "--allow-empty"],
                    cwd=ROOT,
                    capture_output=True,
                    timeout=10,
                )
            subprocess.run(
                ["git", "tag", "-f", tag, "-m", reason or "auto snapshot"], cwd=ROOT, capture_output=True, timeout=5
            )
            results["commit"] = get_current_commit()
            results["git_ok"] = True
            logger.info("✅ Git snapshot %s (commit: %s)", tag, results["commit"])
        except Exception as e:
            logger.error("❌ Git snapshot failed: %s", e)

    # 2. Directory backup
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
        logger.info("✅ Directory backup %s", tag)
        _cleanup_old_backups()
    except Exception as e:
        logger.error("❌ Directory backup failed: %s", e)

    # 3. Write log
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
        logger.error("❌ Cannot write snapshot log: %s", e)


def _cleanup_old_backups():
    try:
        backups = sorted(BACKUP_DIR.glob("snap-*.tar.gz"))
        while len(backups) > MAX_BACKUPS:
            backups.pop(0).unlink()
    except Exception:
        pass


# ── Snapshot List ──


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


# ── Rollback System ──


def rollback_to(tag: str) -> dict:
    """Rollback to a specified snapshot. Git first, then directory backup."""
    result = {"tag": tag, "success": False, "method": "", "message": ""}

    # Method 1: Git rollback
    if git_available():
        try:
            r = subprocess.run(["git", "tag", "-l", tag], cwd=ROOT, capture_output=True, text=True, timeout=5)
            if tag in r.stdout:
                pre = take_snapshot(reason=f"pre-rollback-to-{tag}")
                subprocess.run(["git", "checkout", "--force", tag], cwd=ROOT, capture_output=True, timeout=10)
                result["success"] = True
                result["method"] = "git"
                result["message"] = f"Rolled back to Git tag {tag}"
                result["pre_rollback_tag"] = pre.get("tag", "")
                return result
        except Exception as e:
            result["message"] = f"Git rollback failed: {e}, trying directory backup..."

    # Method 2: Directory backup
    backup_file = BACKUP_DIR / f"{tag}.tar.gz"
    if backup_file.exists():
        try:
            pre = take_snapshot(reason=f"pre-rollback-to-{tag}")
            subprocess.run(["tar", "xzf", str(backup_file), "-C", str(ROOT)], capture_output=True, timeout=30)
            result["success"] = True
            result["method"] = "backup"
            result["message"] = f"Restored from directory backup {tag}.tar.gz"
            result["pre_rollback_tag"] = pre.get("tag", "")
            return result
        except Exception as e:
            result["message"] = f"Directory backup rollback failed: {e}"

    result["message"] = f"Snapshot {tag} not found"
    return result


def rollback_latest() -> dict:
    snapshots = list_snapshots(limit=2)
    if len(snapshots) < 2:
        return {"success": False, "message": "No previous snapshot available for rollback"}
    return rollback_to(snapshots[-2]["tag"])


# ── Health Check ──


def health_check() -> dict:
    issues, suggestions = [], []

    # Git status
    if git_available():
        try:
            r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=5)
            uncommitted = r.stdout.strip()
            if uncommitted:
                n = len(uncommitted.split("\n"))
                if n > 5:
                    issues.append(f"{n} uncommitted files")
                    suggestions.append("Run 'python3 -m lib.lifeline snapshot' to commit")
        except Exception:
            issues.append("Git status check failed")
    else:
        issues.append("Git repository unavailable")

    # main.py syntax
    main_py = ROOT / "main.py"
    if main_py.exists():
        try:
            r = subprocess.run(
                [sys.executable, "-m", "py_compile", str(main_py)], capture_output=True, text=True, timeout=5
            )
            if r.returncode != 0:
                issues.append(f"main.py syntax error: {r.stderr.strip()[:100]}")
                suggestions.append("Rollback immediately: 'python3 -m lib.lifeline rollback_latest'")
        except Exception:
            pass

    # Disk space
    try:
        stat = shutil.disk_usage(ROOT)
        free_gb = stat.free / (1024**3)
        if free_gb < 0.5:
            issues.append(f"Disk space low: {free_gb:.1f}GB")
            suggestions.append("Clean up logs and old backups")
    except Exception:
        pass

    # Critical files
    for f in ["main.py", "lib/kernel.py", "lib/identity.py", "lib/skill_loader.py", "lib/toolkit.py"]:
        if not (ROOT / f).exists():
            issues.append(f"Critical file missing: {f}")
            suggestions.append("Rollback to latest complete snapshot immediately")

    # Rollback frequency detection
    snapshots = list_snapshots(limit=3)
    rb_count = sum(1 for s in snapshots if "rollback" in s.get("reason", ""))
    if rb_count >= 2:
        issues.append(f"{rb_count} rollbacks in last 3 snapshots")
        suggestions.append("Suggest pausing auto-evolution, investigate root cause")

    return {
        "healthy": len(issues) == 0,
        "issues": issues,
        "suggestions": suggestions,
        "commit": get_current_commit(),
        "branch": get_current_branch(),
        "snapshot_count": len(list_snapshots()),
        "timestamp": datetime.now().isoformat(),
    }


# ── Pre-evolution Check ──


def pre_evolution_check() -> dict:
    warnings = []
    snapshots = list_snapshots(limit=1)
    if snapshots:
        hours_since = (datetime.now() - datetime.fromisoformat(snapshots[0]["timestamp"])).total_seconds() / 3600
        if hours_since > 24:
            warnings.append(f"Last snapshot was {hours_since:.0f} hours ago")
    else:
        warnings.append("Never created a snapshot")
    health = health_check()
    if not health["healthy"]:
        warnings.extend(health["issues"])
    return {"pass": len(warnings) == 0, "warnings": warnings, "should_snapshot_first": len(snapshots) == 0}


# ── Auto-snapshot Before Editing Code ──

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
    """Check if a file path belongs to code/config files"""
    fp = filepath.replace("\\", "/")
    return any(pat in fp for pat in CODE_PATTERNS)


def snapshot_before_edit(filepath: str, reason: str = "") -> dict:
    """
    Call this function before editing code.
    It auto-detects file type and takes a snapshot if it's a code/config file.

    Usage:
        result = snapshot_before_edit("main.py", "optimize memory module")
        if result["snapshot_taken"]:
            print(f"Snapshot taken: {result['tag']}")
        # Then safely edit your code
    """
    if not is_code_file(filepath):
        return {
            "snapshot_taken": False,
            "reason": f"{filepath} is not a code file, skipping snapshot",
            "tag": "",
        }

    reason_text = f"Before editing: {reason or filepath}"
    result = take_snapshot(reason=reason_text)

    return {
        "snapshot_taken": True,
        "tag": result["tag"],
        "git_ok": result["git_ok"],
        "backup_ok": result["backup_ok"],
        "reason": reason_text,
        "timestamp": result["timestamp"],
    }


# ── CLI Entry ──


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if len(sys.argv) < 2:
        print("Usage: python3 -m lib.lifeline <command>")
        print("Commands:")
        print("  snapshot [reason]   — Manually take a snapshot")
        print("  list [count]        — View snapshot list")
        print("  rollback <tag>      — Rollback to specified snapshot")
        print("  rollback-latest     — Rollback to previous snapshot")
        print("  check               — Health check")
        print("  tags                — List Git tags")
        print("  edit <path> [reason] — Snapshot before editing code")
        return

    cmd = sys.argv[1]
    if cmd == "snapshot":
        reason = sys.argv[2] if len(sys.argv) > 2 else "manual snapshot"
        print(json.dumps(take_snapshot(reason), indent=2, ensure_ascii=False))
    elif cmd == "list":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        snapshots = list_snapshots(limit)
        if not snapshots:
            print("No snapshot records")
        else:
            print(f"{'Tag':<25} {'Time':<25} {'Reason':<30} {'Git':<10} {'Backup':<10}")
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
            print("Please specify the tag to rollback to")
            return
        print(json.dumps(rollback_to(sys.argv[2]), indent=2, ensure_ascii=False))
    elif cmd == "rollback-latest":
        print(json.dumps(rollback_latest(), indent=2, ensure_ascii=False))
    elif cmd == "check":
        h = health_check()
        if h["healthy"]:
            print("✅ System healthy")
        else:
            print("⚠️  Issues found:")
            for i in h["issues"]:
                print(f"  ❌ {i}")
            if h["suggestions"]:
                print("\nSuggestions:")
                for s in h["suggestions"]:
                    print(f"  💡 {s}")
        print(f"\n  Branch: {h['branch']}\n  Commit: {h['commit']}\n  Snapshots: {h['snapshot_count']}")
    elif cmd == "tags":
        for t in list_git_tags():
            print(t)
    elif cmd == "edit":
        if len(sys.argv) < 3:
            print("Usage: python3 -m lib.lifeline edit <filepath> [reason]")
            return
        filepath = sys.argv[2]
        reason = sys.argv[3] if len(sys.argv) > 3 else ""
        print(json.dumps(snapshot_before_edit(filepath, reason), indent=2, ensure_ascii=False))
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
