# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/query_profiler.py

SQL query performance profiler.
DB query profiler for agent-1 (engineering) + standard edition.
"""

import asyncio
import json
import logging
import os
import sys

from lib.toolkit import tool

logger = logging.getLogger(__name__)
SKILL_DIR = os.path.expanduser("~/.qclaw/skills/YF-query-profiler/scripts")


@tool()
async def profile_database(db_path: str = "", sql: str = "") -> dict:
    """Analyze database performance or a single SQL query, detecting slow queries, full table scans, N+1, and missing indexes.

    Args:
        db_path: SQLite database path (analyze tables/indexes/recommended indexes for the entire database)
        sql: A single SQL query statement (analyze risks and optimization suggestions)

    Returns:
        Analysis report including slow queries, index suggestions, and risk detection
    """
    if not db_path and not sql:
        return {"error": "Must specify db_path or sql parameter"}

    cmd = [
        sys.executable or "python3",
        os.path.join(SKILL_DIR, "profile_queries.py"),
        "--output",
        "/tmp/opprime-query-profile.json",
    ]
    if db_path:
        cmd.extend(["--sqlite", db_path])
    if sql:
        cmd.extend(["--sql", sql])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=os.path.expanduser("~"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        report_path = "/tmp/opprime-query-profile.json"
        if os.path.exists(report_path):
            with open(report_path, encoding="utf-8") as f:
                report = json.load(f)
            os.remove(report_path)
            return report

        return {
            "success": proc.returncode == 0,
            "output": stdout.decode("utf-8", errors="replace")[:2000],
            "errors": stderr.decode("utf-8", errors="replace")[:500],
        }
    except TimeoutError:
        return {"error": "Analysis timed out (30s)"}
    except Exception as e:
        return {"error": str(e)}
