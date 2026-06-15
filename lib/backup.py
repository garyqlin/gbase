# SPDX-License-Identifier: MIT
"""
lib/backup.py

备份系统 — 波段一：刹车装置。

提供：
- 写前自动备份（write_file 调用）
- 备份列表查询
- 备份恢复
- 过期清理
- 核心文件检查点标记
"""

import hashlib
import json
import os
import secrets
import shutil
from datetime import datetime, timedelta

# 默认备份目录：云端 /home/opprime-v2/.backups，本地 ~/opprime/.backups
_default_backup = os.environ.get("GBASE_BACKUP_DIR", "")
if not _default_backup:
    # 自动检测工作目录
    cwd = os.getcwd()
    if "/home/opprime-v2" in cwd:
        _default_backup = "/home/opprime-v2/.backups"
    else:
        _default_backup = os.path.join(os.path.expanduser("~"), "opprime", ".backups")
BACKUP_DIR = _default_backup
INDEX_PATH = os.path.join(BACKUP_DIR, "index.json")

# 核心文件路径（修改前自动创建🔴检查点，而不仅仅是📦备份）
CORE_PATTERNS = [
    # 架构核心
    "/main.py",
    "/lib/",
    "/tools/",
    "/skills/",
    # 宪法与元规则
    "CONSTITUTION.md",
    "META-RULES.md",
    # 进化与记忆
    "evolution-log.md",
    "SYSTEM-MAP.md",
    "/experience/",
    "/memory/",
    "/mirror/",
    # 家目录配置
    "/.bashrc",
    "/.profile",
    # 启动与配置
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
    """生成唯一备份文件名（微秒+随机数防撞）。"""
    rel = filepath.replace("/", "_").replace(" ", "_")
    ts = timestamp.strftime("%Y-%m-%dT%H-%M-%S-%f")  # 加微秒
    rand = secrets.token_hex(4)  # 8 位随机 hex
    h = hashlib.md5(f"{ts}_{filepath}_{rand}".encode()).hexdigest()[:6]
    return f"{ts}_{rel}_{rand}_{h}"


def _is_core_file(filepath: str) -> bool:
    """判断是否为核心文件（需要检查点级备份）。"""
    return any(pattern in filepath for pattern in CORE_PATTERNS)


def backup_file(filepath: str, backup_type: str = "auto") -> str | None:
    """写前备份。文件存在时创建备份，返回 backup_id；不存在返回 None。"""
    if not os.path.exists(filepath):
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)

    now = datetime.now()

    # 核心文件自动升级为检查点
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
    """列出备份，可按原始路径筛选。"""
    index = _load_index()
    backups = index["backups"]

    if filepath:
        backups = [b for b in backups if b["original_path"] == filepath]

    backups.sort(key=lambda b: b["timestamp"], reverse=True)
    return backups[:limit]


def restore_backup(backup_id: str) -> dict:
    """恢复备份到原始位置。恢复前会先备份当前文件。"""
    index = _load_index()

    for b in index["backups"]:
        if b["id"] == backup_id:
            backup_path = os.path.join(BACKUP_DIR, b["backup_file"])
            if not os.path.exists(backup_path):
                return {"error": f"备份文件不存在: {backup_path}"}

            # 恢复前先备份当前文件（防止恢复错）
            if os.path.exists(b["original_path"]):
                backup_file(b["original_path"], backup_type="pre_restore")

            shutil.copy2(backup_path, b["original_path"])
            return {
                "restored": b["original_path"],
                "from_backup": backup_id,
                "timestamp": b["timestamp"],
                "note": "恢复前已自动备份当前文件到 .backups/",
            }

    return {"error": f"未找到备份: {backup_id}"}


def cleanup_backups(days: int = 7) -> dict:
    """清理超过 N 天的备份。"""
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
    """获取备份统计信息。"""
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
