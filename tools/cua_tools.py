# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/cua_tools.py

CUA desktop operation tool.
"""

import asyncio
import logging
import os
import sys

from lib.toolkit import tool

logger = logging.getLogger(__name__)
SKILL_DIR = os.path.expanduser("~/.qclaw/skills/YF-cua-agent/scripts")


@tool()
async def cua_plan(action: str, target: str = "", url: str = "") -> dict:
    """生成 CUA 桌面操作计划（仅规划，不执行）。

    Args:
        action: 操作类型 click|type|scroll|screenshot|navigate
        target: 操作目标描述
        url: 导航目标 URL（仅 navigate 时使用）

    Returns:
        操作计划详情
    """
    cmd = [sys.executable or "python3", os.path.join(SKILL_DIR, "cua_executor.py"), "--action", action]
    if target:
        cmd.extend(["--target", target])
    if url:
        cmd.extend(["--url", url])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=os.path.expanduser("~"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        return {
            "success": proc.returncode == 0,
            "plan": stdout.decode("utf-8", errors="replace")[:3000],
            "errors": stderr.decode("utf-8", errors="replace")[:500],
        }
    except TimeoutError:
        return {"error": "CUA plan generation timed out"}
    except Exception as e:
        return {"error": str(e)}


@tool()
async def cua_execute(action: str, target: str = "", url: str = "") -> dict:
    """执行 CUA 桌面操作（仅计划输出，需要配合 vision 模型执行）。

    Args:
        action: click|type|scroll|screenshot|navigate
        target: 操作描述
        url: URL（navigate 时用）

    Returns:
        执行结果
    """
    return await cua_plan(action, target, url)


@tool()
async def memory_load(date: str = "") -> dict:
    """加载每日记忆摘要。

    Args:
        date: 日期 YYYY-MM-DD（留空=当天）

    Returns:
        记忆摘要内容
    """
    cmd = [
        sys.executable or "python3",
        os.path.expanduser("~/.qclaw/skills/YF-daily-memory-loader/scripts/load_daily_memory.py"),
        "--action",
        "load",
    ]
    if date:
        cmd.extend(["--date", date])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        return {
            "success": proc.returncode == 0,
            "content": stdout.decode("utf-8", errors="replace")[:3000],
            "errors": stderr.decode("utf-8", errors="replace")[:500],
        }
    except Exception as e:
        return {"error": str(e)}


@tool()
async def memory_status() -> dict:
    """查看记忆文件统计信息。"""
    cmd = [
        sys.executable or "python3",
        os.path.expanduser("~/.qclaw/skills/YF-daily-memory-loader/scripts/load_daily_memory.py"),
        "--action",
        "status",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return {"success": True, "status": stdout.decode("utf-8", errors="replace")[:2000]}
    except Exception as e:
        return {"error": str(e)}


@tool()
async def exec_safe(command: str) -> dict:
    """安全执行 shell 命令，捕获输出和返回码。

    Args:
        command: 要执行的命令

    Returns:
        执行结果（stdout/stderr/返回码/耗时）
    """
    cmd = [
        sys.executable or "python3",
        os.path.expanduser("~/.qclaw/skills/YF-exec-harness/scripts/exec_safe.py"),
        "--action",
        "run",
        "--cmd",
        command,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=35)
        return {
            "success": proc.returncode == 0,
            "output": stdout.decode("utf-8", errors="replace")[:3000],
            "errors": stderr.decode("utf-8", errors="replace")[:500],
            "returncode": proc.returncode,
        }
    except TimeoutError:
        return {"error": "Command timed out (30s)"}
    except Exception as e:
        return {"error": str(e)}


@tool()
async def manga_video_plan(files: str = "", script: str = "") -> dict:
    """生成漫画转视频制作计划。

    Args:
        files: 图片路径（支持通配符如 "*.png"）
        script: 旁白文字

    Returns:
        视频制作计划
    """
    cmd = [
        sys.executable or "python3",
        os.path.expanduser("~/.qclaw/skills/YF-manga-video/scripts/manga_to_video.py"),
        "--action",
        "plan",
    ]
    if files:
        cmd.extend(["--files", files])
    if script:
        cmd.extend(["--script", script])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        return {
            "success": proc.returncode == 0,
            "plan": stdout.decode("utf-8", errors="replace")[:3000],
            "errors": stderr.decode("utf-8", errors="replace")[:500],
        }
    except Exception as e:
        return {"error": str(e)}


@tool()
async def manga_video_estimate(pages: int = 20, audio_min: int = 3) -> dict:
    """估算漫画转视频时长。

    Args:
        pages: 漫画页数
        audio_min: 音频时长（分钟）

    Returns:
        估算结果
    """
    cmd = [
        sys.executable or "python3",
        os.path.expanduser("~/.qclaw/skills/YF-manga-video/scripts/manga_to_video.py"),
        "--action",
        "estimate",
        "--pages",
        str(pages),
        "--audio_min",
        str(audio_min),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return {"success": True, "estimate": stdout.decode("utf-8", errors="replace")[:1000]}
    except Exception as e:
        return {"error": str(e)}
