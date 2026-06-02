# SPDX-License-Identifier: MIT
"""
gbase/tools/network_tools.py

Network diagnostic tool.
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
    """Check network connection status (port/HTTP/DNS/Ping).

    Args:
        action: Diagnostic type — port (port check), http (HTTP reachability), dns (DNS resolution), ping (latency test)
        target: Target address (host:port / URL / domain / IP)

    Returns:
        Network diagnostic result
    """
    script = _build_skill_path()
    cmd = ["python3", script, "--action", action]

    if action == "port":
        # target format: "host:port"
        parts = target.split(":")
        if len(parts) == 2:
            cmd.extend(["--host", parts[0], "--port", parts[1]])
        else:
            return {"error": f"Port check requires host:port format, got: {target}"}
    elif action == "http":
        cmd.extend(["--url", target])
    elif action == "dns":
        cmd.extend(["--domain", target])
    elif action == "ping":
        cmd.extend(["--host", target])
    else:
        return {"error": f"Unsupported diagnostic type: {action}, supported: port/http/dns/ping"}

    logger.info("Running network diagnostics: %s %s %s", script, action, target)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            return {"error": f"Diagnostic failed: {stderr.decode().strip()}"}
        return {"result": stdout.decode().strip()}
    except TimeoutError:
        return {"error": "Network diagnostic timed out (>30s)"}
    except FileNotFoundError:
        return {"error": f"Skill script not found: {script}"}
    except Exception as e:
        logger.exception("check_network exception")
        return {"error": str(e)}
