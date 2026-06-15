# SPDX-License-Identifier: MIT
"""
tools/rollback.py

回滚工具 — 波段一：刹车装置。

提供：
- rollback_list: 列出备份
- rollback_restore: 恢复到指定备份
- rollback_cleanup: 清理过期备份
- rollback_stats: 备份统计

用法（LLM 调用）：
    rollback_list(filepath="tools/write_file.py")  # 查看某文件的备份历史
    rollback_restore(backup_id="2026-05-16T12-30-00_...")  # 恢复到某个备份
"""

from lib.backup import backup_stats, cleanup_backups, list_backups, restore_backup
from lib.toolkit import tool


@tool()
async def rollback_list(filepath: str = "", limit: int = 20) -> dict:
    """列出备份历史。

    Args:
        filepath: 可选，筛选特定文件的备份（留空列出全部）
        limit: 最多返回条数（默认 20）

    Returns:
        {backups: [...], total: N}
    """
    backups = list_backups(filepath, limit)
    return {"backups": backups, "total": len(backups), "note": "用 rollback_restore(backup_id) 恢复某个备份"}


@tool()
async def rollback_restore(backup_id: str) -> dict:
    """恢复指定备份到原始位置。

    恢复前会自动备份当前文件（防止恢复错）。

    Args:
        backup_id: 备份 ID（从 rollback_list 获取）

    Returns:
        {restored: 文件路径, from_backup: 备份ID, ...} 或 {error: ...}
    """
    return restore_backup(backup_id)


@tool()
async def rollback_cleanup(days: int = 7) -> dict:
    """清理过期备份。

    Args:
        days: 保留最近 N 天的备份（默认 7）

    Returns:
        {removed: 清理数量, kept: 保留数量}
    """
    return cleanup_backups(days)


@tool()
async def rollback_stats() -> dict:
    """查看备份系统统计信息。

    Returns:
        {total_backups, total_size_mb, by_type, backup_dir}
    """
    return backup_stats()
