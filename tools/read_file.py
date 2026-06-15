# SPDX-License-Identifier: MIT
"""
tools/read_file.py — 读取本地文件工具。
"""

import logging
import os

from lib.territory import check_rescue_permission, check_territory_violation
from lib.toolkit import tool

logger = logging.getLogger(__name__)

# 白名单：优先读环境变量 OPPRIME_ALLOWED_ROOTS，没有才用默认
# 默认只允许读取 POSEIDON_HOME 及 /tmp/
_POSEIDON_HOME = os.environ.get("POSEIDON_HOME", os.path.expanduser("~/poseidon-home"))
_env_roots = os.environ.get("OPPRIME_ALLOWED_ROOTS")
if _env_roots:
    _ALLOWED_PREFIXES = [os.path.abspath(os.path.expanduser(p.strip())) for p in _env_roots.split(":") if p.strip()]
else:
    # 硬编码白名单（fallback，环境变量传不进去时兜底）
    _ALLOWED_PREFIXES = [
        os.path.abspath(os.path.expanduser(p))
        for p in [
            "~/.qclaw",
            "~/opprime",
            "~/glink",
            "~/Projects",
            "~/lancer",
            "~/gundam-home",
            "~/poseidon-home",
            "~/gstudio",
            "~/.claude",
            "~/homeassistant-latest",
            "/Volumes/workspace",
            "~/games",
            "~/Desktop",
        ]
    ]


@tool()
async def read_file(
    filepath: str = "", file_path: str = "", path: str = "", offset: int = 0, max_chars: int = 0
) -> dict:
    """读取本地文件内容。

    用于读取 workspace 中的配置文件、skill 的 SKILL.md 等本地文本文件。
    不适用于网络 URL（用 fetch_page 读取网页）。

    Args:
        filepath: 文件路径（相对路径或绝对路径）
        file_path: 兼容参数名（与 filepath 等价）
        path: 兼容参数名（与 filepath 等价）
        offset: 跳过前 offset 字节（默认0）。用于分批读取大文件。
        max_chars: 限制返回字符数，0 表示完整读取（默认）。超出部分截断。
    """
    try:
        if file_path and not filepath:
            filepath = file_path
        if path and not filepath:
            filepath = path
        expanded = os.path.expanduser(filepath)
        abs_path = os.path.abspath(expanded)

        # 白名单检查
        if not any(abs_path.startswith(p) for p in _ALLOWED_PREFIXES):
            denied_msg = f"拒绝读取: {abs_path} 不在白名单内。只允许读取这些目录：{_ALLOWED_PREFIXES}"
            logger.warning("📛 " + denied_msg)
            return {
                "error": denied_msg,
                "path": abs_path,
                "allowed_roots": _ALLOWED_PREFIXES,
                "hint": "白名单由 OPPRIME_ALLOWED_ROOTS 环境变量控制。如果这个文件你需要读，请告诉主人扩展白名单。",
            }

        # 领地检查（阻塞跨领地读取，除非走救援白名单）
        violation = check_territory_violation(filepath)
        if violation:
            # 检查是否在救援白名单内
            if not check_rescue_permission(violation, abs_path, "rescue"):
                denied_msg = (
                    f"🚫 领地侵犯拒绝: 不能读取 Agent「{violation}」的文件 {abs_path}\n"
                    f"这是其他 Agent 的领地。需要救援时，请用 rescue_tool 的 "
                    f"check_brother / read_brother_log 工具，它们通过救援白名单受限访问。"
                )
                logger.warning(denied_msg)
                return {
                    "error": denied_msg,
                    "path": abs_path,
                    "violation": violation,
                    "hint": "救援访问请使用 check_brother() 或 read_brother_log() 工具",
                }
            else:
                logger.warning("📖 救援模式读取: rescue白名单通过了 Agent「%s」的文件 %s", violation, abs_path)

        if not os.path.exists(abs_path):
            return {"error": f"文件不存在: {filepath}", "path": abs_path}

        if not os.path.isfile(abs_path):
            return {"error": f"路径不是文件: {filepath}", "path": abs_path}

        file_size = os.path.getsize(abs_path)

        # 防御：LLM 可能传字符串参数（如 max_chars="5000"）
        if isinstance(offset, str):
            try:
                offset = int(offset)
            except (ValueError, TypeError):
                offset = 0
        if isinstance(max_chars, str):
            try:
                max_chars = int(max_chars)
            except (ValueError, TypeError):
                max_chars = 0

        read_limit = None if max_chars == 0 else min(max_chars, 800000)

        with open(abs_path, encoding="utf-8", errors="replace") as f:
            if offset > 0:
                f.seek(offset)
            content = f.read(read_limit)

        truncated = read_limit is not None and len(content) >= read_limit

        current_pos = (offset if offset > 0 else 0) + len(content)
        end_of_file = current_pos >= file_size or not truncated

        return {
            "path": abs_path,
            "size": file_size,
            "content": content,
            "truncated": truncated,
            "end_of_file": end_of_file,
            "note": (f"[全文] {file_size} 字节" if end_of_file else f"截断: 已读 {current_pos}/{file_size} 字节"),
        }
    except PermissionError:
        return {"error": "无权限读取文件", "path": filepath}
    except Exception as e:
        return {"error": f"读取失败: {str(e)}", "path": filepath}
