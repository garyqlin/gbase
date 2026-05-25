# SPDX-License-Identifier: MIT
"""
tools/write_file.py

Write file tool. Symmetric to read_file, for LLM file creation/modification.
Safety constraints:
- Only writes within opprime workspace directories
- Auto-creates parent directories
- [Evolution #7] Pre-write auto-backup: original file saved to .backups/ on overwrite
"""

import os

from lib.backup import BACKUP_DIR, _is_core_file, backup_file
from lib.toolkit import tool

# Allowed root directories for writing
# Prefer environment variable for cross-env deployment without code changes
_env_roots = os.environ.get("GBASE_ALLOWED_ROOTS")
ALLOWED_ROOTS = [r.strip() for r in _env_roots.split(":") if r.strip()] if _env_roots else [os.path.expanduser("~/")]


def _resolve_path(filepath: str) -> tuple[str, str]:
    """Resolve file path, returns (absolute_path, error_message)."""
    expanded = os.path.expanduser(filepath)
    expanded = os.path.abspath(expanded)

    allowed = False
    for root in ALLOWED_ROOTS:
        resolved_root = os.path.abspath(os.path.expanduser(root))
        if expanded.startswith(resolved_root + "/") or expanded == resolved_root:
            allowed = True
            break

    if not allowed:
        return "", (
            f"Write rejected: path {expanded} is not in allowed scope.\n"
            f"Allowed root directories:\n" + "\n".join(f"  - {r}" for r in ALLOWED_ROOTS)
        )

    parent = os.path.dirname(expanded)
    os.makedirs(parent, exist_ok=True)

    return expanded, ""


def _build_context(filepath: str) -> str:
    """Build a file context summary (for the LLM to decide whether to continue editing)."""
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return "(File just created, no content yet)"

    lines = content.split("\n")
    total = len(lines)

    if total <= 20:
        preview = content
    else:
        first = "\n".join(lines[:5])
        last = "\n".join(lines[-5:])
        preview = f"{first}\n... ({total - 10} lines omitted) ...\n{last}"

    return f"File written ({total} lines, {len(content)} chars):\n```\n{preview}\n```"


@tool()
async def write_file(filepath: str, content: str, mode: str = "w") -> dict:
    """Create or modify a file. Auto-backup before overwrite (if backup module enabled).

    Args:
        filepath: File path (absolute or relative to ~/opprime)
        content: File content (text)
        mode: Write mode, 'w' for overwrite (default), 'a' for append

    Returns:
        Dict with write results: path / size / context / backup / error
    """
    path, err = _resolve_path(filepath)
    if err:
        return {"error": err}

    if mode not in ("w", "a"):
        return {"error": f"Unsupported mode: {mode}, only 'w' (overwrite) or 'a' (append) allowed"}

    # ── Phase 1: Pre-write auto-backup ──
    backup_id = None
    backup_note = ""
    if mode == "w" and os.path.exists(path):
        backup_id = backup_file(path)
        if backup_id:
            is_core = _is_core_file(path)
            tag = "🔴Checkpoint" if is_core else "📦Backup"
            backup_note = f"\n{tag}: original file backed up to {BACKUP_DIR}/{backup_id}"

    try:
        with open(path, mode, encoding="utf-8") as f:
            f.write(content)

        size = os.path.getsize(path)
        context = _build_context(path)

        result = {
            "path": path,
            "size": size,
            "mode": mode,
            "context": context,
            "backup": backup_note.strip() if backup_note else None,
            "note": "To continue editing this file, call write_file again (mode='w' to overwrite).",
        }
        if backup_id:
            result["backup_id"] = backup_id
            result["restore_cmd"] = f"Use rollback_restore(backup_id='{backup_id}') to rollback"

        return result
    except Exception as e:
        return {"error": f"Write failed: {e}"}
