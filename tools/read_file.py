# SPDX-License-Identifier: MIT
"""
tools/read_file.py — 读取本地文件工具。
供 LLM 按需读取 SKILL.md、AGENTS.md 等 workspace 文件。
安全约束：只允许在 ALLOWED_ROOTS 目录内读取。
"""

import logging
import os

from lib.territory import check_territory_violation
from lib.toolkit import tool

logger = logging.getLogger(__name__)

# 允许读取的根目录（取自环境变量 OPPRIME_ALLOWED_ROOTS，: 分隔）
_env_roots = os.environ.get("OPPRIME_ALLOWED_ROOTS")
ALLOWED_ROOTS = (
    [r.strip() for r in _env_roots.split(":") if r.strip()]
    if _env_roots
    else [
        os.path.expanduser("~/.qclaw"),
        os.path.expanduser("~/gbase"),
        os.path.expanduser("~/glink"),
        os.path.expanduser("~/Projects"),
        os.path.expanduser("~/lancer"),
        os.path.expanduser("~/gbase-home"),
        os.path.expanduser("~/gstudio"),
        os.path.expanduser("~/.claude"),
        os.path.expanduser("~/homeassistant-latest"),
        "/Volumes/workspace",
        os.path.expanduser("~/games"),
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("$GBASE_DESKTOP"),
        "/tmp",
        "/private/tmp",
    ]
)


@tool()
async def read_file(filepath: str, offset: int = 0, max_chars: int = 0) -> dict:
    """读取本地文件内容。

    用于读取 workspace 中的配置文件、skill 的 SKILL.md 等本地文本文件。
    不适用于网络 URL（用 fetch_page 读取网页）。

    Args:
        filepath: 文件路径（相对路径或绝对路径）
        offset: 跳过前 offset 字节（默认0）。用于分批读取大文件。
        max_chars: 限制返回字符数，0 表示完整读取（默认）。超出部分截断。
    """
    try:
        expanded = os.path.expanduser(filepath)
        abs_path = os.path.abspath(expanded)

        # 路径安全检查：必须在 ALLOWED_ROOTS 内
        resolved_roots = [os.path.abspath(os.path.expanduser(r)) for r in ALLOWED_ROOTS]
        if not any(abs_path.startswith(root) for root in resolved_roots):
            return {
                "error": "路径不在允许的读取范围内",
                "path": abs_path,
                "allowed_roots": resolved_roots,
            }

        # 领地检查（只警告不阻塞——只读不写是安全的）
        violation = check_territory_violation(filepath)
        if violation:
            logger.warning(
                "📖 跨越领地读取: %s 读取了 Agent「%s」的文件 %s",
                abs_path, violation, abs_path
            )

        if not os.path.exists(abs_path):
            return {"error": f"文件不存在: {filepath}", "path": abs_path}

        if not os.path.isfile(abs_path):
            return {"error": f"路径不是文件: {filepath}", "path": abs_path}

        file_size = os.path.getsize(abs_path)

        read_limit = None if max_chars == 0 else min(max_chars, 800000)

        with open(abs_path, encoding="utf-8", errors="replace") as f:
            if offset > 0:
                f.seek(offset)
            content = f.read(read_limit)

        truncated = read_limit is not None and len(content) >= read_limit

        # 判断是否到文件结尾
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
