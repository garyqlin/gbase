# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/query_profiler.py

YF-query-profiler 集成：SQL 查询性能分析。
为重锤（工程臂）+ 标准版提供数据库优化能力。
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
    """分析数据库性能或单条 SQL 查询，检测慢查询、全表扫描、N+1、缺失索引。

    Args:
        db_path: SQLite 数据库路径（分析整库的表/索引/推荐索引）
        sql: 单条 SQL 查询语句（分析风险和优化建议）

    Returns:
        分析报告含慢查询、索引建议、风险检测
    """
    if not db_path and not sql:
        return {"error": "需要指定 db_path 或 sql 参数"}

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
            with open(report_path) as f:
                report = json.load(f)
            os.remove(report_path)
            return report

        return {
            "success": proc.returncode == 0,
            "output": stdout.decode("utf-8", errors="replace")[:2000],
            "errors": stderr.decode("utf-8", errors="replace")[:500],
        }
    except TimeoutError:
        return {"error": "分析超时（30秒）"}
    except Exception as e:
        return {"error": str(e)}
