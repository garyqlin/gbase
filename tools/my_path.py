# SPDX-License-Identifier: MIT
"""
tools/my_path.py

Path awareness: let LLM know its location.
"""

import os
from pathlib import Path

from lib.toolkit import tool

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@tool()
async def my_current_path() -> dict:
    """Show current working directory and project structure. Call when you don't know where you are."""
    return {
        "project_root": str(_PROJECT_ROOT),
        "current_directory": os.getcwd(),
        "data_directory": str(_PROJECT_ROOT / "data"),
        "skills_directory": str(_PROJECT_ROOT / "skills"),
        "identities_directory": str(_PROJECT_ROOT / "identities"),
        "exec_root": str(_PROJECT_ROOT),
    }


@tool()
async def my_project_roots() -> dict:
    """Show project root path info."""
    return {
        "project_root": str(_PROJECT_ROOT),
        "getcwd": os.getcwd(),
        "exec_allowed": str(_PROJECT_ROOT),
        "data_dir": str(_PROJECT_ROOT / "data"),
        "sessions_dir": str(_PROJECT_ROOT / "data" / "sessions"),
    }
