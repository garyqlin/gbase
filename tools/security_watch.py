# SPDX-License-Identifier: MIT
"""
gbase/tools/security_watch.py

YF-security-scanner 集成：本地安全扫描。
所有战甲通用，特别适合大黄蜂（扫描第三方代码）+ 重锤（扫描项目代码）。
"""

import asyncio
import logging
import os
import re
import sys

from lib.toolkit import tool

logger = logging.getLogger(__name__)
SKILL_DIR = os.environ.get(
    "YF_SECURITY_SCANNER_DIR",
    os.path.expanduser("~/.qclaw/skills/YF-security-scanner/scripts"),
)


@tool()
async def security_scan_directory(directory: str, output: str = "") -> dict:
    """扫描指定目录的安全漏洞（密钥泄露、依赖CVE、代码模式）。

    Args:
        directory: 要扫描的目录路径
        output: 报告输出路径（可选，默认自动生成）

    Returns:
        扫描结果摘要（高危/中危/低风险数量）
    """
    # Sanitize directory path to prevent injection
    safe_dir = re.sub(r'[;&|`$]', '', directory.strip())
    if not safe_dir:
        return {"error": "Directory path is empty after sanitization"}
    if not os.path.isdir(safe_dir):
        return {"error": f"Directory not found: {safe_dir}"}

    cmd = [
        sys.executable or "python3",
        os.path.join(SKILL_DIR, "security_scan.py"),
        "--dir",
        safe_dir,
    ]
    if output:
        cmd.extend(["--output", output])
    else:
        cmd.extend(["--output", "/tmp/gbase-security-scan.json"])

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

        # 解析结果摘要
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
    except asyncio.TimeoutError:
        return {"success": False, "error": "安全扫描超时（60秒）"}
    except Exception as e:
        return {"success": False, "error": str(e)}
