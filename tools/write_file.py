# SPDX-License-Identifier: MIT
"""
tools/write_file.py

Write file tool. Symmetric to read_file, for LLM file creation/modification.
安全约束：
- 只在 opprime workspace 目录下写
- 自动创建父目录
- 【第7次进化】写前自动备份：覆盖写时原文件自动存入 .backups/
"""

import os

from lib.backup import BACKUP_DIR, _is_core_file, backup_file
from lib.toolkit import tool

# 允许写入的根目录
# 优先从环境变量读取（跨环境部署无需改代码）
_env_roots = os.environ.get("GBASE_ALLOWED_ROOTS")
if _env_roots:
    ALLOWED_ROOTS = [r.strip() for r in _env_roots.split(":") if r.strip()]
else:
    ALLOWED_ROOTS = [
        os.path.expanduser("~/opprime/opprime-core-v2"),
        os.path.expanduser("~/opprime"),
        "/home/opprime-v2",                # 云端运行目录
        "/var/spool/cron/crontabs",        # 允许写系统 crontab (macOS 无此目录)
    ]


def _resolve_path(filepath: str) -> tuple[str, str]:
    """解析文件路径，返回 (绝对路径, 错误信息)。"""
    expanded = os.path.expanduser(filepath)
    expanded = os.path.abspath(expanded)

    allowed = False
    for root in ALLOWED_ROOTS:
        resolved_root = os.path.abspath(os.path.expanduser(root))
        if expanded.startswith(resolved_root + "/") or expanded == resolved_root:
            allowed = True
            break

    if not allowed:
        return "", (
            f"写入被拒绝：路径 {expanded} 不在允许范围内。\n"
            f"允许的根目录：\n"
            + "\n".join(f"  - {r}" for r in ALLOWED_ROOTS)
        )

    parent = os.path.dirname(expanded)
    os.makedirs(parent, exist_ok=True)

    return expanded, ""


def _build_context(filepath: str) -> str:
    """构建文件上下文摘要（供 LLM 判断是否还要继续改）。"""
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return "(文件刚创建，暂无内容)"

    lines = content.split("\n")
    total = len(lines)

    if total <= 20:
        preview = content
    else:
        first = "\n".join(lines[:5])
        last = "\n".join(lines[-5:])
        preview = f"{first}\n... (中间 {total - 10} 行) ...\n{last}"

    return f"文件已写入 ({total} 行, {len(content)} 字符):\n```\n{preview}\n```"


@tool()
async def write_file(filepath: str, content: str, mode: str = "w") -> dict:
    """创建或修改文件。写入前自动备份（如果启用了 backup 模块）。

    Args:
        filepath: 文件路径（绝对路径或相对于 ~/opprime 的相对路径）
        content: 文件内容（文本）
        mode: 写入模式，'w' 覆盖写入（默认），'a' 追加

    Returns:
        包含写入结果的字典：path / size / context / backup / error
    """
    path, err = _resolve_path(filepath)
    if err:
        return {"error": err}

    if mode not in ("w", "a"):
        return {"error": f"不支持的 mode: {mode}，仅支持 'w'（覆盖）或 'a'（追加）"}

    # ── 波段一：写前自动备份 ──
    backup_id = None
    backup_note = ""
    if mode == "w" and os.path.exists(path):
        backup_id = backup_file(path)
        if backup_id:
            is_core = _is_core_file(path)
            tag = "🔴检查点" if is_core else "📦备份"
            backup_note = f"\n{tag}: 原文件已备份到 {BACKUP_DIR}/{backup_id}"

    try:
        with open(path, mode, encoding="utf-8") as f:
            f.write(content)

        size = os.path.getsize(path)
        context = _build_context(path)

        result = {
            "path": path,
            "size": size,
            "mode": mode,
            "context": context,
            "backup": backup_note.strip() if backup_note else None,
            "note": "如需继续编辑此文件，再次调用 write_file 即可（mode='w' 覆盖）。"
        }
        if backup_id:
            result["backup_id"] = backup_id
            result["restore_cmd"] = f"用 rollback_restore(backup_id='{backup_id}') 可回滚"

        return result
    except Exception as e:
        return {"error": f"写入失败: {e}"}
