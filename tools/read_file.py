# SPDX-License-Identifier: MIT
"""
Local file reader tool.
供 LLM 按需读取 SKILL.md、AGENTS.md 等 workspace 文件。
"""

import logging
import os

from lib.toolkit import tool

logger = logging.getLogger(__name__)


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

        # 判断是否到文件结尾: 当前偏移+已读 >= 文件大小
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
