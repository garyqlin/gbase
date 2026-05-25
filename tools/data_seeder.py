# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/data_seeder.py

Smart test data generator.
Demo data seeder for agent-3 (research arm).
"""

import asyncio
import logging
import os
import sys

from lib.toolkit import tool

logger = logging.getLogger(__name__)
SKILL_DIR = os.path.expanduser("~/.qclaw/skills/YF-data-seeder/scripts")


@tool()
async def seed_test_data(
    columns: str, count: int = 15, format: str = "json", table: str = "mock_data", output: str = ""
) -> dict:
    """Generate batch test/demo data. Supported field types: name, email, phone, int, float, date, address, company, city, etc.

    Args:
        columns: Column description, format like "name: name, email: email, status: active,inactive, age: int:18-65"
        count: Number of records (default 15, aligned with the recommended 5-15 records for client delivery)
        format: Output format: json / csv / sql
        table: Table name for SQL mode
        output: Output file path (optional)

    Returns:
        Generated data content (JSON or SQL text)
    """
    cmd = [
        sys.executable or "python3",
        os.path.join(SKILL_DIR, "seed_data.py"),
        "--describe",
        columns,
        "--count",
        str(count),
        "--format",
        format,
        "--table",
        table,
    ]
    if output:
        cmd.extend(["--output", output])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=os.path.expanduser("~"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")

        # Extract the generated data portion
        data_start = out.find("[")
        if data_start == -1:
            data_start = out.find("INSERT INTO")
        data = out[data_start:].strip() if data_start >= 0 else ""

        return {
            "success": proc.returncode == 0,
            "count": count,
            "format": format,
            "table": table,
            "data": data[:5000],
            "full_output": out[:1000],
            "errors": err[:500] if err else "",
        }
    except TimeoutError:
        return {"error": "Data generation timed out (30s)"}
    except Exception as e:
        return {"error": str(e)}
