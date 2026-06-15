# SPDX-License-Identifier: MIT
"""
lib/path_safety.py — 统一路径安全校验

所有直接写文件的工具（docx_gen / pptx_gen / xlsx_gen 等）
都应调用此模块检查路径是否在白名单内。

兼容多实例：通过 AGENT_HOME 环境变量或当前工作目录自动检测。
"""

import logging
import os

logger = logging.getLogger(__name__)


# ── 安全范围自动检测 ──
def _detect_home() -> str:
    """自动检测 Agent home 目录"""
    for env_key in ("AGENT_HOME", "GANJIANG_HOME", "OPPRIME_HOME"):
        val = os.environ.get(env_key)
        if val and os.path.isdir(val):
            return os.path.abspath(os.path.expanduser(val))
    # fallback: 从当前工作目录向上找
    cwd = os.getcwd()
    return os.path.abspath(cwd)


_AGENT_HOME = _detect_home()

_ALLOWED_PREFIXES = [
    os.path.abspath(os.path.expanduser(_AGENT_HOME)),
    "/tmp/",
]


def validate_output_path(output_path: str) -> dict:
    """校验输出路径是否安全。

    Args:
        output_path: 用户指定的输出路径

    Returns:
        {"ok": True, "path": abs_path} 或 {"ok": False, "error": "..."}
    """
    expanded = os.path.expanduser(output_path)
    abs_path = os.path.abspath(expanded)

    if not any(abs_path.startswith(p) for p in _ALLOWED_PREFIXES):
        return {
            "ok": False,
            "error": (
                f"拒绝写入: {output_path} 不在白名单内。"
                f"只允许写入 {_AGENT_HOME} 及 /tmp/ 目录。"
                f"请将 output_path 改为 agent home 下的路径。"
            ),
        }

    # territory check (optional, soft fail if territory.py not available)
    try:
        from lib.territory import build_territory_error, check_territory_violation

        violation = check_territory_violation(abs_path)
        if violation:
            return {
                "ok": False,
                "error": build_territory_error(violation, abs_path, "写入"),
            }
    except ImportError:
        pass  # territory module not available in all instances

    return {"ok": True, "path": abs_path}
