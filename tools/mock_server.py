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

# Track active mock server processes
_active_mock_servers: dict[int, dict] = {}


@tool()
async def start_mock_server(description: str, port: int = 4000) -> dict:
    """Start a local Mock API server for independent work before backend development.

    Args:
        description: API description, e.g. "GET /api/users, POST /api/users, GET /api/users/1, DELETE /api/users/1"
        port: Port number (default 4000)

    Returns:
        Server process information and status
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

        # Wait for startup confirmation (2 seconds)
        await asyncio.sleep(2)

        # Check if still running
        if proc.returncode is not None:
            stdout, stderr = await proc.communicate()
            return {
                "success": False,
                "error": "Server failed to start",
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
    """Stop the running Mock API server.

    Args:
        port: Port of the service to stop (default 4000)

    Returns:
        Stop result
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
