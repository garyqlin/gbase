# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/mock_server.py

Local mock API server.
Mock server for agent-2 (design arm).
"""

import asyncio
import logging
import os
import signal
import sys

from lib.toolkit import tool

logger = logging.getLogger(__name__)
SKILL_DIR = os.path.expanduser("~/.qclaw/skills/YF-api-mock-server/scripts")

# 跟踪已启动的 mock server 进程
_active_mock_servers: dict[int, dict] = {}


@tool()
async def start_mock_server(description: str, port: int = 4000) -> dict:
    """启动本地 Mock API 服务器，后端开发前可独立工作。

    Args:
        description: API 描述，如 "GET /api/users, POST /api/users, GET /api/users/1, DELETE /api/users/1"
        port: 端口号（默认4000）

    Returns:
        服务器进程信息和状态
    """
    cmd = [
        sys.executable or "python3",
        os.path.join(SKILL_DIR, "run_mock_server.py"),
        "--describe",
        description,
        "--port",
        str(port),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=os.path.expanduser("~"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # 等待启动确认（2秒）
        await asyncio.sleep(2)

        # 检查是否正在运行
        if proc.returncode is not None:
            stdout, stderr = await proc.communicate()
            return {
                "success": False,
                "error": "服务器启动失败",
                "output": stdout.decode()[:1000],
                "errors": stderr.decode()[:500],
            }

        _active_mock_servers[port] = {
            "pid": proc.pid,
            "port": port,
            "description": description,
            "proc": proc,
        }

        return {
            "success": True,
            "pid": proc.pid,
            "port": port,
            "endpoint": f"http://localhost:{port}",
            "description": description,
            "note": f"Mock server running on http://localhost:{port}, call stop_mock_server({port}) to stop",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@tool()
async def stop_mock_server(port: int = 4000) -> dict:
    """停止正在运行的 Mock API 服务器。

    Args:
        port: 要停止的服务端口（默认4000）

    Returns:
        停止结果
    """
    server = _active_mock_servers.pop(port, None)
    if server:
        try:
            os.kill(server["pid"], signal.SIGTERM)
            return {"success": True, "port": port, "message": f"Mock server on port {port} stopped"}
        except ProcessLookupError:
            return {"success": True, "port": port, "message": "Server already stopped"}
    else:
        return {
            "success": False,
            "message": f"No active mock server on port {port}. Try 'lsof -ti :{port} | xargs kill' to force stop.",
        }
