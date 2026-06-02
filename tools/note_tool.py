"""note_tool.py — L4 笔记系统：主动知识存储，不衰减，可查询。

与 mirror 不同：note 不经过 strength 衰减体系，写入后永不遗忘。
每条 note 有独立文件，按日期 + 标题组织。支持全文搜索。
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from lib.toolkit import tool

# ── 笔记存储目录的路径解析用函数，未用模块级常量（避免 import 时固化） ──
# 目录级锁：note_write 并发安全
_note_lock = asyncio.Lock()


def _notes_dir() -> Path:
    base = Path(os.environ.get("GBASE_DATA_DIR", "data"))
    notes = base / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    return notes


def _safe_title(title: str) -> str:
    """将标题转成安全的文件名片段"""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:60]


def _parse_observations(note: dict) -> list:
    """安全读取 observations 字段，遇到非 list 类型则重置为空列表"""
    obs = note.get("observations")
    if not isinstance(obs, list):
        return []
    return obs


def _parse_datetime(dt: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> datetime | None:
    """安全解析日期字符串，返回 None 表示解析失败"""
    try:
        return datetime.strptime(dt, fmt)
    except (ValueError, TypeError):
        return None


@tool()
async def note_write(title: str, content: str, tags: str = "", source: str = ""):
    """写一条笔记到 L4 笔记系统。笔记不会被 strength 衰减，写入后永不遗忘。

    Args:
        title: 笔记标题（简短概括，≤100字）
        content: 笔记正文（什么值得记住）
        tags: 逗号分隔的标签，方便分类搜索
        source: 来源描述（如"与用户对话学到"、"从trace提炼"）
    """
    try:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        filename = f"{date_str}_{_safe_title(title)}.json"
        filepath = _notes_dir() / filename

        note = {
            "title": title,
            "content": content,
            "tags": [t.strip() for t in tags.split(",") if t.strip()],
            "source": source,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        # 并发安全：asyncio.Lock 保证同一进程内串行
        # （跨进程场景仍需文件级锁，当前 GBase 部署均为单进程，暂不处理）
        async with _note_lock:
            if filepath.exists():
                raw = filepath.read_text(encoding="utf-8")
                existing = json.loads(raw)
                existing_obs = _parse_observations(existing)
                # 如果 content 不同，追加为 observations
                if existing.get("content") != content:
                    existing_obs.append({"content": content, "source": source, "at": timestamp})
                    existing["observations"] = existing_obs
                    existing["updated_at"] = timestamp
                    note = existing

            filepath.write_text(json.dumps(note, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "result": f"笔记已保存: {filename}",
            "file": filename,
            "tags": note["tags"],
        }
    except (OSError, json.JSONDecodeError, PermissionError, UnicodeEncodeError) as e:
        return {"error": f"笔记写入失败: {e}"}


@tool()
async def note_search(query: str, max_results: int = 10):
    """搜索笔记系统中的笔记。按标题、内容、标签匹配。

    Args:
        query: 搜索关键词
        max_results: 最多返回几条（默认10）
    """
    query = query.lower()
    results = []
    notes_dir = _notes_dir()
    if not notes_dir.exists():
        return {"result": "暂无笔记", "total": 0, "notes": []}

    for f in sorted(notes_dir.glob("*.json"), reverse=True):
        try:
            note = json.loads(f.read_text(encoding="utf-8"))
            title = note.get("title", "").lower()
            content = note.get("content", "").lower()
            tags = " ".join(note.get("tags", [])).lower()
            observations = _parse_observations(note)

            # 搜索命中
            hits_title = query in title
            hits_content = query in content
            hits_tags = query in tags
            hits_obs = any(query in obs.get("content", "").lower() for obs in observations)
            if hits_title or hits_content or hits_tags or hits_obs:
                results.append(
                    {
                        "file": f.name,
                        "title": note.get("title", ""),
                        "created_at": note.get("created_at", ""),
                        "content_summary": note.get("content", "")[:100],
                        "tags": note.get("tags", []),
                        "observations": len(observations),
                    }
                )
                if len(results) >= max_results:
                    break
        except (json.JSONDecodeError, KeyError, AttributeError):
            continue

    return {"result": f"找到 {len(results)} 条相关笔记", "total": len(results), "notes": results}


@tool()
async def note_list(tag: str = "", days: int = 7):
    """列出最近的笔记。

    Args:
        tag: 按标签筛选（可选）
        days: 最近几天（默认7天）
    """
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=days)
    results = []
    notes_dir = _notes_dir()
    if not notes_dir.exists():
        return {"result": "暂无笔记", "notes": []}

    for f in sorted(notes_dir.glob("*.json"), reverse=True):
        try:
            note = json.loads(f.read_text(encoding="utf-8"))
            created = _parse_datetime(note.get("created_at", ""))
            if created is None or created < cutoff:
                continue
            if tag and tag not in " ".join(note.get("tags", [])):
                continue
            results.append(
                {
                    "title": note.get("title", ""),
                    "created_at": note.get("created_at", ""),
                    "tags": note.get("tags", []),
                    "source": note.get("source", ""),
                }
            )
        except (json.JSONDecodeError, KeyError, AttributeError):
            continue

    return {"result": f"最近 {days} 天 {len(results)} 条笔记", "notes": results}
