# SPDX-License-Identifier: MIT
"""
tools/rollback.py

Rollback tool — emergency brake.

Provides:
- rollback_list: list backups
- rollback_restore: restore to a specific backup
- rollback_cleanup: clean up expired backups
- rollback_stats: backup statistics

Usage (LLM calls):
    rollback_list(filepath="tools/write_file.py")  # view backup history for a file
    rollback_restore(backup_id="2026-05-16T12-30-00_...")  # restore to a backup
"""

from lib.backup import backup_stats, cleanup_backups, list_backups, restore_backup
from lib.toolkit import tool


@tool()
async def rollback_list(filepath: str = "", limit: int = 20) -> dict:
    """List backup history.

    Args:
        filepath: Optional, filter backups for a specific file (empty = list all)
        limit: Max number of entries to return (default 20)

    Returns:
        {backups: [...], total: N}
    """
    backups = list_backups(filepath, limit)
    return {"backups": backups, "total": len(backups), "note": "Use rollback_restore(backup_id) to restore a backup"}


@tool()
async def rollback_restore(backup_id: str) -> dict:
    """Restore a specific backup to its original location.

    The current file is automatically backed up before restore (to prevent restoring the wrong file).

    Args:
        backup_id: Backup ID (obtained from rollback_list)

    Returns:
        {restored: file path, from_backup: backup ID, ...} or {error: ...}
    """
    return restore_backup(backup_id)


@tool()
async def rollback_cleanup(days: int = 7) -> dict:
    """Clean up expired backups.

    Args:
        days: Keep backups from the last N days (default 7)

    Returns:
        {removed: count removed, kept: count kept}
    """
    return cleanup_backups(days)


@tool()
async def rollback_stats() -> dict:
    """View backup system statistics.

    Returns:
        {total_backups, total_size_mb, by_type, backup_dir}
    """
    return backup_stats()
