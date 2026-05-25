# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/security_watch.py

Local security scanner.
All agents. Agent-3 scans third-party code, agent-1 scans project code.
"""

import asyncio
import logging
import os
import sys

from lib.toolkit import tool

logger = logging.getLogger(__name__)
SKILL_DIR = os.path.expanduser("~/.qclaw/skills/YF-security-scanner/scripts")


@tool()
async def security_scan_directory(directory: str, output: str = "") -> dict:
    """扫描指定目录的安全漏洞（密钥泄露、依赖CVE、代码模式）。
    
    Args:
        directory: 要扫描的Directory path
        output: Report output path (optional, auto-generates)
    
    Returns:
        Scan summary (critical/medium/low counts)
    """
    cmd = [
        sys.executable or "python3",
        os.path.join(SKILL_DIR, "security_scan.py"),
        "--dir", directory,
    ]
    if output:
        cmd.extend(["--output", output])
    else:
        cmd.extend(["--output", "/tmp/opprime-security-scan.json"])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=os.path.expanduser("~"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        # Parse result summary
        findings = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0}
        for line in stdout_text.split("\n"):
            for level in findings:
                if f"  {level}:" in line:
                    try:
                        findings[level] = int(line.split(f"{level}:")[1].strip().split()[0])
                    except:
                        pass

        return {
            "success": proc.returncode == 0,
            "directory": directory,
            "findings": findings,
            "summary": stdout_text[:3000],
            "errors": stderr_text[:500] if stderr_text else "",
        }
    except TimeoutError:
        return {"success": False, "error": "Security scan timeout (60s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}
