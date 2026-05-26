# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/log_profiler.py

Multi-source log analyzer.
Suitable for agent-3 (research) + standard edition.
"""

import asyncio
import json
import logging
import os
import sys

from lib.toolkit import tool

logger = logging.getLogger(__name__)
SKILL_DIR = os.path.expanduser("~/.qclaw/skills/YF-log-analyzer/scripts")


@tool()
async def analyze_log_file(file_path: str, log_format: str = "auto", slow_threshold_ms: int = 1000) -> dict:
    """Analyze a log file for error patterns, slow requests, and resource alerts.

    Args:
        file_path: Log file path
        log_format: Log format: auto / text / json
        slow_threshold_ms: Slow request threshold in ms (default 1000)

    Returns:
        Analysis report (error stats, slow requests, resource alerts)
    """
    cmd = [
        sys.executable or "python3",
        os.path.join(SKILL_DIR, "analyze_logs.py"),
        "--file",
        file_path,
        "--format",
        log_format,
        "--slow-threshold",
        str(slow_threshold_ms),
        "--output",
        "/tmp/opprime-log-analysis.json",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=os.path.expanduser("~"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        # Read JSON report
        report_path = "/tmp/opprime-log-analysis.json"
        if os.path.exists(report_path):
            with open(report_path, encoding="utf-8") as f:
                report = json.load(f)
            os.remove(report_path)
            return report

        return {
            "success": proc.returncode == 0,
            "stdout": stdout.decode("utf-8", errors="replace")[:2000],
            "stderr": stderr.decode("utf-8", errors="replace")[:500],
        }
    except TimeoutError:
        return {"success": False, "error": "Log analysis timed out (15s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}
