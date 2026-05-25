# SPDX-License-Identifier: MIT
"""
lib/backup.py

Backup system — Phase one: safety brake.

Provides:
- auto-backup before write (called by write_file)
- list backups
- restore backups
- expiry cleanup
- core file checkpoint marking
"""

import hashlib
import json
import os
import secrets
import shutil
from datetime import datetime, timedelta

# Default backup dir: use GBASE_BACKUP_DIR env or project root/.backups
_default_backup = os.environ.get("GBASE_BACKUP_DIR", "")
if not _default_backup:
    # auto-detect working directory
    cwd = os.getcwd()
    _default_backup = os.getenv("GBASE_BACKUP_DIR", "") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".backups")
BACKUP_DIR = _default_backup
INDEX_PATH = os.path.join(BACKUP_DIR, "index.json")

# Core file paths (auto-create checkpoints before modification, not just backups)
CORE_PATTERNS = [
    # architecture core
    "/main.py",
    "/lib/",
    "/tools/",
    "/skills/",
    # constitution and meta-rules
    "CONSTITUTION.md",
    "META-RULES.md",
    # evolution and memory
    "evolution-log.md",
    "SYSTEM-MAP.md",
    "/experience/",
    "/memory/",
    "/mirror/",
    # home directory config
    "/.bashrc",
    "/.profile",
    # startup and config
    "/systemd/",
    "/config/",
]


def _load_index() -> dict:
    if not os.path.exists(INDEX_PATH):
        return {"backups": []}
    try:
        with open(INDEX_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {"backups": []}


def _save_index(index: dict):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(INDEX_PATH, "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def _make_backup_id(filepath: str, timestamp: datetime) -> str:
    """Generate unique backup filename (microsecond + random salt for collision avoidance)."""
    rel = filepath.replace("/", "_").replace(" ", "_")
    ts = timestamp.strftime("%Y-%m-%dT%H-%M-%S-%f")  # microsecond
    rand = secrets.token_hex(4)  # 8 hex chars
    h = hashlib.md5(f"{ts}_{filepath}_{rand}".encode()).hexdigest()[:6]
    return f"{ts}_{rel}_{rand}_{h}"


def _is_core_file(filepath: str) -> bool:
    """Check if file is core (needs checkpoint-level backup)."""
    return any(pattern in filepath for pattern in CORE_PATTERNS)


def backup_file(filepath: str, backup_type: str = "auto") -> str | None:
    """Backup before write. Creates backup if file exists, returns backup_id; returns None if not."""
    if not os.path.exists(filepath):
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)

    now = datetime.now()

    # Core files auto-upgrade to checkpoint
    if backup_type == "auto" and _is_core_file(filepath):
        backup_type = "checkpoint"

    backup_id = _make_backup_id(filepath, now)
    backup_path = os.path.join(BACKUP_DIR, backup_id)

    shutil.copy2(filepath, backup_path)

    index = _load_index()
    index["backups"].append(
        {
            "id": backup_id,
            "original_path": filepath,
            "backup_file": backup_id,
            "timestamp": now.isoformat(),
            "size": os.path.getsize(filepath),
            "type": backup_type,
        }
    )
    _save_index(index)

    return backup_id


def list_backups(filepath: str = "", limit: int = 20) -> list[dict]:
    """List backups, filterable by original path."""
    index = _load_index()
    backups = index["backups"]

    if filepath:
        backups = [b for b in backups if b["original_path"] == filepath]

    backups.sort(key=lambda b: b["timestamp"], reverse=True)
    return backups[:limit]


def restore_backup(backup_id: str) -> dict:
    """Restore backup to original location. Current file is backed up first."""
    index = _load_index()

    for b in index["backups"]:
        if b["id"] == backup_id:
            backup_path = os.path.join(BACKUP_DIR, b["backup_file"])
            if not os.path.exists(backup_path):
                return {"error": f"Backup file not found: {backup_path}"}

            # Backup current file before restore (safety)
            if os.path.exists(b["original_path"]):
                backup_file(b["original_path"], backup_type="pre_restore")

            shutil.copy2(backup_path, b["original_path"])
            return {
                "restored": b["original_path"],
                "from_backup": backup_id,
                "timestamp": b["timestamp"],
                "note": "Current file auto-backed up to .backups/ before restore",
            }

    return {"error": f"Backup not found: {backup_id}"}


def cleanup_backups(days: int = 7) -> dict:
    """Clean up backups older than N days."""
    cutoff = datetime.now() - timedelta(days=days)

    index = _load_index()
    kept = []
    removed = 0

    for b in index["backups"]:
        try:
            ts = datetime.fromisoformat(b["timestamp"])
        except (ValueError, KeyError):
            kept.append(b)
            continue

        if ts < cutoff:
            backup_path = os.path.join(BACKUP_DIR, b["backup_file"])
            if os.path.exists(backup_path):
                os.remove(backup_path)
            removed += 1
        else:
            kept.append(b)

    index["backups"] = kept
    _save_index(index)

    return {"removed": removed, "kept": len(kept)}


def backup_stats() -> dict:
    """Get backup statistics."""
    index = _load_index()
    backups = index["backups"]

    total_size = sum(b.get("size", 0) for b in backups)
    by_type = {}
    for b in backups:
        t = b.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    return {
        "total_backups": len(backups),
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "by_type": by_type,
        "backup_dir": BACKUP_DIR,
    }
