# SPDX-License-Identifier: MIT
"""
Local file reader tool.
Allows LLM to read SKILL.md, AGENTS.md, and other workspace files on demand.
"""

import logging
import os

from lib.toolkit import tool

# Allowed root directories for reading
# Prefer environment variable for cross-env deployment without code changes
_env_roots = os.environ.get("GBASE_ALLOWED_ROOTS")
ALLOWED_ROOTS = [r.strip() for r in _env_roots.split(":") if r.strip()] if _env_roots else [os.path.expanduser("~/")]

logger = logging.getLogger(__name__)


@tool()
async def read_file(filepath: str, offset: int = 0, max_chars: int = 0) -> dict:
    """Read local file content.

    For reading config files, skill SKILL.md files, and other local text files in workspace.
    Not for network URLs (use fetch_page for web pages).

    Args:
        filepath: File path (relative or absolute)
        offset: Skip first offset bytes (default 0). For reading large files in chunks.
        max_chars: Max characters to return, 0 means full read (default). Excess is truncated.
    """
    try:
        expanded = os.path.expanduser(filepath)
        abs_path = os.path.abspath(expanded)

        # Path scope check (same as write_file)
        allowed = False
        for root in ALLOWED_ROOTS:
            resolved_root = os.path.abspath(os.path.expanduser(root))
            if abs_path.startswith(resolved_root + "/") or abs_path == resolved_root:
                allowed = True
                break
        if not allowed:
            return {"error": f"Read rejected: path {abs_path} is not in allowed scope"}

        if not os.path.exists(abs_path):
            return {"error": f"File not found: {filepath}", "path": abs_path}

        if not os.path.isfile(abs_path):
            return {"error": f"Path is not a file: {filepath}", "path": abs_path}

        file_size = os.path.getsize(abs_path)

        read_limit = None if max_chars == 0 else min(max_chars, 800000)

        with open(abs_path, encoding="utf-8", errors="replace") as f:
            if offset > 0:
                f.seek(offset)
            content = f.read(read_limit)

        truncated = read_limit is not None and len(content) >= read_limit

        # Check if end of file reached: current offset + bytes read >= file size
        current_pos = (offset if offset > 0 else 0) + len(content)
        end_of_file = current_pos >= file_size or not truncated

        return {
            "path": abs_path,
            "size": file_size,
            "content": content,
            "truncated": truncated,
            "end_of_file": end_of_file,
            "note": (f"[Full] {file_size} bytes" if end_of_file else f"Truncated: {current_pos}/{file_size} bytes read"),
        }
    except PermissionError:
        return {"error": "Permission denied", "path": filepath}
    except Exception as e:
        return {"error": f"Read failed: {str(e)}", "path": filepath}
