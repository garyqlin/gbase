"""Territory safety — cross-agent read/write access control.

Configured via environment variables:
  GBASE_HOME  — this agent's home directory
  GBASE_AGENT_NAME  — this agent's name
  GBASE_AGENT_HOMES — colon-separated list of agent_name:path pairs
"""

import logging
import os

logger = logging.getLogger(__name__)

_my_home = os.environ.get("GBASE_HOME", os.path.expanduser("~"))
_my_name = os.environ.get("GBASE_AGENT_NAME", "agent")

# Parse colon-separated agent homes from env
_agent_homes_raw = os.environ.get("GBASE_AGENT_HOMES", "")
AGENT_HOMES: dict[str, str] = {}
if _agent_homes_raw:
    for pair in _agent_homes_raw.split(":"):
        if "=" in pair:
            name, home = pair.split("=", 1)
            AGENT_HOMES[name.strip()] = home.strip()


def _is_other_agent_territory(path: str) -> str | None:
    """If path points to another agent's home, return that agent's name."""
    path = os.path.abspath(os.path.expanduser(path))
    for name, home in AGENT_HOMES.items():
        if name == _my_name:
            continue
        if path.startswith(home):
            return name
    return None


def _check_territory(path: str, my_home: str | None = None) -> None:
    """Check if path belongs to another agent. Raises PermissionError if write, logs warning if read."""
    invader = _is_other_agent_territory(path)
    if invader:
        logger.warning("⚠️ 试图操作 %s 的领地: %s", invader, path)
        raise PermissionError(f"[领地安全] {path} 属于 {invader}，不允许操作。")


def check_territory_violation(path: str) -> str | None:
    """Check if path belongs to another agent. Returns agent name if violation, None if safe.

    Unlike _check_territory, this does NOT raise — caller decides how to handle.
    """
    invader = _is_other_agent_territory(path)
    return invader


def build_territory_error(violation: str, path: str, action: str = "操作") -> str:
    """Build a human-readable territory error message."""
    return f"领地安全拒绝：路径 {path} 属于 Agent「{violation}」，不允许{action}。如需跨 Agent 协作，请通过共享目录。"
