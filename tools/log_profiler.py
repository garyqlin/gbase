# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/log_profiler.py

YF-log-analyzer 集成：多源日志分析工具。
适合Bumblebee (research arm) + 标准版。
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
    """分析日志文件，检测错误模式、慢请求、资源告警。

    Args:
        file_path: 日志文件路径
        log_format: 日志格式 auto / text / json
        slow_threshold_ms: 慢请求阈值（毫秒，默认1000）

    Returns:
        分析报告（错误统计、慢请求、资源告警）
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

        # 读取 JSON 报告
        report_path = "/tmp/opprime-log-analysis.json"
        if os.path.exists(report_path):
            with open(report_path) as f:
                report = json.load(f)
            os.remove(report_path)
            return report

        return {
            "success": proc.returncode == 0,
            "stdout": stdout.decode("utf-8", errors="replace")[:2000],
            "stderr": stderr.decode("utf-8", errors="replace")[:500],
        }
    except TimeoutError:
        return {"success": False, "error": "日志分析超时（15秒）"}
    except Exception as e:
        return {"success": False, "error": str(e)}
