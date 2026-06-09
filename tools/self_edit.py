# SPDX-License-Identifier: MIT
"""
self_edit.py — Agent 自修代码工具

让 Agent 能安全地修改自己的源代码（tools/、lib/ 下的 .py 文件）。
核心安全机制：
  1. 改前自动备份到 ~/.gbase_rollback/
  2. 改后自动语法检查
  3. 回退支持（rollback_restore）
  4. 只能改 ~/gbase-home/ 内的文件

用法：
  self_edit(path="tools/exec.py", old="旧代码片段", new="新代码片段")
  self_edit(path="tools/exec.py", search="搜索替换（整段替换）", replace="替换内容")
  self_edit(path="tools/exec.py", insert_after="行号或文本匹配", content="插入内容")
  self_edit_verify(path="tools/exec.py")  ← 语法检查
  self_edit_rollback(path="tools/exec.py")  ← 回退到最近备份
"""

import ast
import hashlib
import os
import shutil
import time
from pathlib import Path

from lib.toolkit import tool

# ── 安全范围界定 ──
_INSTANCE_HOME = Path(__file__).resolve().parent.parent  # ~/gbase-home/
_ALLOWED_DIRS = [
    _INSTANCE_HOME / "tools",
    _INSTANCE_HOME / "lib",
    _INSTANCE_HOME / "rules",
    _INSTANCE_HOME / "cron",
    _INSTANCE_HOME / "data",
    _INSTANCE_HOME / "tools",
    _INSTANCE_HOME / "channels",
]

# lib/ 子目录也放开
_LIB_DIRS = [_INSTANCE_HOME / "lib" / d for d in ["channels", "identity"]]
_ALLOWED_DIRS.extend([d for d in _LIB_DIRS if d.exists()])
# 额外允许的 lib 目录（共享底座的可写副本）
_LIB_SHARED = Path("$HOME/gbase/lib")
if _LIB_SHARED.exists():
    _ALLOWED_DIRS.append(_LIB_SHARED)

_ROLLBACK_DIR = _INSTANCE_HOME / ".gbase_rollback"
_ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)


# ── 路径校验 ──
def _safety_check(path: str) -> tuple[Path, str]:
    """解析并验证路径在安全范围内。返回 (绝对路径, 错误信息)"""
    raw = Path(path)
    if raw.suffix != ".py":
        return None, "仅支持 .py 文件修改"

    abs_path = raw.resolve() if raw.is_absolute() else (_INSTANCE_HOME / raw).resolve()

    # 必须在允许目录下
    for allowed in _ALLOWED_DIRS:
        try:
            abs_path.relative_to(allowed)
            return abs_path, None
        except ValueError:
            continue

    return None, f"路径不在安全范围内。允许的目录：{', '.join(str(d) for d in _ALLOWED_DIRS)}"


def _backup(path: Path) -> str:
    """改前备份，返回备份文件名"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    content_hash = hashlib.md5(path.read_bytes()).hexdigest()[:8]

    rel = path.relative_to(_INSTANCE_HOME)
    backup_name = f"{rel.as_posix().replace('/', '__')}.{ts}.{content_hash}.bak"
    backup_path = _ROLLBACK_DIR / backup_name
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    shutil.copy2(path, backup_path)

    # 清理旧备份：只保留最近 20 个版本的备份
    all_baks = sorted(_ROLLBACK_DIR.glob(f"{rel.as_posix().replace('/', '__')}.*.bak"))
    while len(all_baks) > 20:
        all_baks[0].unlink()
        all_baks = all_baks[1:]

    return backup_name


def _verify_syntax(path: Path) -> tuple[bool, str]:
    """语法检查"""
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
        ast.parse(source)
        return True, "语法检查通过"
    except SyntaxError as e:
        return False, f"语法错误: {e}"
    except Exception as e:
        return False, f"检查失败: {e}"


# ── 工具函数 ──


@tool()
async def self_edit(
    path: str,
    old: str = "",
    new: str = "",
    search: str = "",
    replace: str = "",
    insert_after: str = "",
    content: str = "",
) -> dict:
    """安全地修改自己的源码文件

    支持三种模式：
    A. 精确替换（old → new）— 限 1 次匹配，精准安全
    B. 整段替换（search → replace）— 如果 search 匹配到多处会报错
    C. 行后插入（insert_after 匹配文本 → 在匹配行后插入 content）

    Args:
        path: 文件路径（相对于 ~/gbase-home/ 或绝对路径）
        old: 模式 A — 要替换的原始文本
        new: 模式 A — 替换后的新文本
        search: 模式 B — 要搜索整段文本
        replace: 模式 B — 替换后的文本
        insert_after: 模式 C — 在此文本所在行之后插入
        content: 模式 C — 要插入的内容
    """
    abs_path, err = _safety_check(path)
    if err:
        return {"success": False, "error": err}

    if not abs_path.exists():
        return {"success": False, "error": f"文件不存在: {abs_path}"}

    original = abs_path.read_text(encoding="utf-8")

    # ── 模式选择 ──
    if old and new:
        # A: 精确替换
        count = original.count(old)
        if count == 0:
            return {"success": False, "error": "未找到匹配文本（0次匹配）", "path": str(abs_path)}
        elif count > 1:
            return {
                "success": False,
                "error": f"匹配到 {count} 处，太模糊，请用更精确的文本或改用 search+replace 模式",
                "path": str(abs_path),
            }
        modified = original.replace(old, new, 1)

    elif search and replace:
        # B: 整段替换
        count = original.count(search)
        if count == 0:
            return {"success": False, "error": "未找到搜索文本（0次匹配）", "path": str(abs_path)}
        elif count > 1:
            return {
                "success": False,
                "error": f"搜索文本匹配到 {count} 处，请用更精确的文本",
                "path": str(abs_path),
            }
        modified = original.replace(search, replace, 1)

    elif insert_after and content:
        # C: 行后插入
        lines = original.split("\n")
        matched_idx = -1
        for i, line in enumerate(lines):
            if insert_after in line:
                matched_idx = i
                break
        if matched_idx == -1:
            return {"success": False, "error": f"未找到包含「{insert_after}」的行", "path": str(abs_path)}
        indent = " " * (len(lines[matched_idx]) - len(lines[matched_idx].lstrip()) + 4)
        content_lines = content.split("\n")
        indented_content = "\n".join([(indent + cl) if cl.strip() else "" for cl in content_lines])
        lines.insert(matched_idx + 1, indented_content)
        modified = "\n".join(lines)

    else:
        return {
            "success": False,
            "error": "请提供 old+new（精确替换）或 search+replace（整段替换）或 insert_after+content（行后插入）",
        }

    # ── 改前备份 ──
    backup_name = _backup(abs_path)

    # ── 写入 ──
    abs_path.write_text(modified, encoding="utf-8")

    # ── 语法检查 ──
    syntax_ok, syntax_msg = _verify_syntax(abs_path)
    if not syntax_ok:
        # 语法错误 → 自动回滚
        abs_path.write_text(original, encoding="utf-8")
        return {
            "success": False,
            "error": f"修改后语法错误，已自动回滚: {syntax_msg}",
            "backup": backup_name,
            "path": str(abs_path),
        }

    # ── 返回结果 ──
    return {
        "success": True,
        "path": str(abs_path),
        "backup": backup_name,
        "syntax_check": "通过",
        "note": "修改成功。如需回滚，请调用 self_edit_rollback(path=...)",
        "tip": "修完 bug 后调用 self_edit_remember_reason(root_cause=..., fix_type=..., file_path=...) 来记一条 Knowledge，下次遇到同类问题能直接回忆。",
    }


@tool()
async def self_edit_verify(path: str) -> dict:
    """语法检查工具源码文件（不改内容，只验证语法）

    Args:
        path: 文件路径（相对于 ~/gbase-home/ 或绝对路径）
    """
    abs_path, err = _safety_check(path)
    if err:
        return {"success": False, "error": err}

    if not abs_path.exists():
        return {"success": False, "error": f"文件不存在: {abs_path}"}

    ok, msg = _verify_syntax(abs_path)
    return {
        "success": ok,
        "path": str(abs_path),
        "message": msg,
        "size": abs_path.stat().st_size,
    }


@tool()
async def self_edit_rollback(path: str, version: str = "") -> dict:
    """回滚到之前备份的版本

    Args:
        path: 文件路径（相对于 ~/gbase-home/ 或绝对路径）
        version: 可选，指定备份文件中的特定时间戳或哈希
    """
    abs_path, err = _safety_check(path)
    if err:
        return {"success": False, "error": err}

    rel = abs_path.relative_to(_INSTANCE_HOME)
    pattern = f"{rel.as_posix().replace('/', '__')}.*.bak"
    backups = sorted(_ROLLBACK_DIR.glob(pattern), reverse=True)

    if not backups:
        return {"success": False, "error": "没有找到可回滚的备份", "path": str(abs_path)}

    if version:
        # 按版本匹配
        target = [b for b in backups if version in b.name]
        if not target:
            return {
                "success": False,
                "error": f"未找到匹配版本「{version}」的备份",
                "available_versions": [b.name for b in backups[:10]],
            }
        restore_path = target[0]
    else:
        restore_path = backups[0]  # 最新备份

    # 备份当前文件（防误操作）
    _backup(abs_path)

    # 恢复
    shutil.copy2(restore_path, abs_path)

    # 语法检查
    ok, msg = _verify_syntax(abs_path)
    return {
        "success": ok,
        "path": str(abs_path),
        "restored_from": restore_path.name,
        "syntax_check": "通过" if ok else f"失败: {msg}",
    }


@tool()
async def self_edit_restart() -> dict:
    """重启 Agent 进程（launchd 自动拉起）

    修改 lib/ 下的代码后需要重启才能生效。
    launchd KeepAlive 配置会在进程退出后自动重新拉起。
    返回后会延迟 2 秒自杀，launchd 接管自动拉起。
    """
    import threading

    current_pid = os.getpid()

    def _delayed_exit():
        import time

        time.sleep(2.0)
        os._exit(0)

    threading.Thread(target=_delayed_exit, daemon=True).start()

    return {
        "success": True,
        "message": f"将在 2 秒后重启进程 (PID={current_pid})，launchd 自动拉起",
        "pid": current_pid,
    }


@tool()
async def self_edit_read_source(path: str, offset: int = 0, max_chars: int = 8000) -> dict:
    """读取自己的源码文件（tools/、lib/ 下的 .py 文件）

    Agent 的 read_file 主要用于读外部文件（用户项目、文档等）。
    这个工具专门用于读自己的源码，方便定位和修复 bug。

    Args:
        path: 文件路径（相对于 ~/gbase-home/ 或绝对路径）
        offset: 跳过多少字符（默认 0）
        max_chars: 最多读取多少字符（默认 8000，设 0 表示全量）
    """
    abs_path, err = _safety_check(path)
    if err:
        return {"success": False, "error": err, "path": str(abs_path) if abs_path else path}

    if not abs_path.exists():
        return {"success": False, "error": f"文件不存在: {abs_path}"}

    content = abs_path.read_text(encoding="utf-8")
    total = len(content)

    if max_chars > 0 and offset + max_chars < total:
        content = content[offset : offset + max_chars]
        truncated = True
    elif offset > 0:
        content = content[offset:]
        truncated = False
    else:
        truncated = False

    return {
        "success": True,
        "path": str(abs_path),
        "total_chars": total,
        "content": content,
        "truncated": truncated,
        "offset": offset,
        "size": abs_path.stat().st_size,
    }


@tool()
async def self_edit_list_backups(path: str = "") -> dict:
    """列出文件或全部备份记录

    Args:
        path: 可选，指定文件路径查看其备份历史（不填则显示全部）
    """
    if path:
        abs_path, err = _safety_check(path)
        if err:
            return {"success": False, "error": err}
        rel = abs_path.relative_to(_INSTANCE_HOME)
        pattern = f"{rel.as_posix().replace('/', '__')}.*.bak"
        backups = sorted(_ROLLBACK_DIR.glob(pattern), reverse=True)
        return {
            "success": True,
            "path": str(abs_path),
            "backups": [b.name for b in backups[:30]],
            "total": len(backups),
        }
    else:
        all_baks = sorted(_ROLLBACK_DIR.iterdir(), reverse=True) if _ROLLBACK_DIR.exists() else []
        return {
            "success": True,
            "backup_dir": str(_ROLLBACK_DIR),
            "total": len(all_baks),
            "recent_backups": [b.name for b in all_baks[:50]],
        }


@tool()
async def self_edit_remember_reason(
    root_cause: str,
    fix_type: str = "variable_init",
    file_path: str = "",
    description: str = "",
) -> dict:
    """记录修 bug 经验到 Knowledge，下次同类问题自动回忆。

    修完 bug 后调用这个来沉淀经验，L2 记忆会自动注入。

    Args:
        root_cause: 被修的 bug 根因描述（30-200 字）
        fix_type: 修复类型（variable_init | import_fix | path_fix | timeout_fix | exception_handling | type_error | other）
        file_path: 被修改的文件路径（如 "lib/kernel.py"）
        description: 可选补充说明
    """
    try:
        from lib.storage import Storage

        _st = Storage()
        _summary = f"[自修] {root_cause[:80]}"
        _detail = f"类型: {fix_type}"
        if file_path:
            _detail += f"\n文件: {file_path}"
        _detail += f"\n根因: {root_cause}"
        if description:
            _detail += f"\n说明: {description}"

        ts = int(time.time())
        with _st._lock:
            if _st._conn is not None:
                _st._conn.execute(
                    "INSERT OR IGNORE INTO entries (type, summary, detail, confidence, created_at, hits) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    ("knowledge", _summary[:300], _detail[:2000], "high", ts),
                )
                _st._conn.commit()
                return {
                    "success": True,
                    "summary": _summary[:300],
                    "note": "Knowledge 已记录，下次同类问题 L2 自动注入",
                }
        return {"success": False, "error": "数据库连接不可用"}
    except Exception as e:
        return {"success": False, "error": f"记录失败: {e}"}
