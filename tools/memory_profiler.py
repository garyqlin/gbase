# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/memory_profiler.py

内存分析工具 — 对接 YF-memory-profiler skill。
"""

import asyncio
import logging

from lib.toolkit import tool

logger = logging.getLogger(__name__)

SKILL_DIR = "skills/YF-memory-profiler"
SCRIPT = "scripts/profile_memory.py"


def _build_skill_path() -> str:
    """根据运行环境推测 skill 脚本路径。"""
    import os

    # 优先相对路径（与 tools/ 同级的 skills/）
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, SKILL_DIR, SCRIPT)
    if os.path.exists(path):
        return path
    # 备选：~/.qclaw/skills/
    fallback = os.path.expanduser(f"~/.qclaw/skills/{SKILL_DIR}/{SCRIPT}")
    if os.path.exists(fallback):
        return fallback
    # 最后尝试，可能调用时 cd 到正确目录
    return os.path.join(base, SKILL_DIR, SCRIPT)


@tool()
async def analyze_memory(pid: int = 0, watch: bool = False) -> dict:
    """分析进程内存使用，检测泄漏风险。

    Args:
        pid: 目标进程 PID（0 表示列出所有可分析的进程）
        watch: 是否持续采样监控（每隔30s采样一次，共5次）

    Returns:
        内存分析结果（JSON 格式）
    """
    script = _build_skill_path()
    cmd = ["python3", script]

    if pid > 0:
        cmd.extend(["--pid", str(pid)])
    if watch:
        cmd.append("--watch")

    logger.info("执行内存分析: %s", " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            return {"error": f"内存分析失败: {stderr.decode().strip()}"}
        output = stdout.decode().strip()
        return {"result": output}
    except TimeoutError:
        return {"error": "内存分析超时（>120秒）"}
    except FileNotFoundError:
        return {"error": f"找不到 skill 脚本: {script}"}
    except Exception as e:
        logger.exception("analyze_memory 异常")
        return {"error": str(e)}
