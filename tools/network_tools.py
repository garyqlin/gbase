# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/network_tools.py

网络诊断工具 — 对接 YF-network-analyzer skill。
"""

import asyncio
import logging

from lib.toolkit import tool

logger = logging.getLogger(__name__)

SKILL_DIR = "skills/YF-network-analyzer"
SCRIPT = "scripts/diagnose_network.py"


def _build_skill_path() -> str:
    import os

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, SKILL_DIR, SCRIPT)
    if os.path.exists(path):
        return path
    fallback = os.path.expanduser(f"~/.qclaw/skills/{SKILL_DIR}/{SCRIPT}")
    if os.path.exists(fallback):
        return fallback
    return os.path.join(base, SKILL_DIR, SCRIPT)


@tool()
async def check_network(action: str, target: str) -> dict:
    """检查网络连接状态（端口/HTTP/DNS/Ping）。

    Args:
        action: 诊断类型 — port（端口检查）、http（HTTP可达性）、dns（DNS解析）、ping（延迟测试）
        target: 目标地址（host:port / URL / 域名 / IP）

    Returns:
        网络诊断结果
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", action]

    if action == "port":
        # target 格式: "host:port"
        parts = target.split(":")
        if len(parts) == 2:
            cmd.extend(["--host", parts[0], "--port", parts[1]])
        else:
            return {"error": f"端口检查需要 host:port 格式，收到: {target}"}
    elif action == "http":
        cmd.extend(["--url", target])
    elif action == "dns":
        cmd.extend(["--domain", target])
    elif action == "ping":
        cmd.extend(["--host", target])
    else:
        return {"error": f"不支持的诊断类型: {action}，可选: port/http/dns/ping"}

    logger.info("执行网络诊断: %s %s %s", script, action, target)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"error": f"诊断失败: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "网络诊断超时（>30秒）"}
    except FileNotFoundError:
        return {"error": f"找不到 skill 脚本: {script}"}
    except Exception as e:
        logger.exception("check_network 异常")
        return {"error": str(e)}
