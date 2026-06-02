# SPDX-License-Identifier: MIT
"""
gbase/tools/knowledge.py

知识沉淀工具 — LLM 主动调用，判断什么值得记住。

remember_fact: 存一条不变的事实到 knowledge
search_knowledge: 全文搜索已有 knowledge
search_knowledge_batch: 批量搜索
export_knowledge_md: 导出全部知识为 Markdown（人类可读）
"""

import json
import logging
import time

from lib.toolkit import get_global, tool

logger = logging.getLogger(__name__)


@tool()
async def remember_fact(fact: str, category: str = "general", tags: str = "", update_id: int = None) -> dict:
    """记得一条事实——你自己判断这是值得长期记住的、不会过时的信息。

    什么时候该用：
    - 用户告诉你他的个人/家庭信息（名字、关系、住址等）
    - 你查到了系统的永久配置（域名、端口、路径映射、API 地址等）
    - 用户用了一条你之前不知道的命令/工具/习惯用法
    - 你发现了某个组件/服务的固定工作方式
    - 需要更新一条已有事实时，传 update_id

    什么时候不该用：
    - 有时效性的信息（天气、股价、新闻、事件时间表）
    - 一次性的对话内容
    - 当前进程的资源状态（PID、内存占用等）

    Args:
        fact: 要记住的事实描述，一句话，清晰完整。
        category: 分类。可选值：user / system / workflow / tool / general
        tags: 逗号分隔的标签，方便搜索。例如 "nginx,port,config"
        update_id: 如果要更新已有记录，传该记录的 id。
                   旧事实会自动存入 history 时间线，不可删除。
    """
    storage = get_global("storage")
    if not storage:
        return {"error": "存储引擎未初始化"}

    now = time.time()

    # ── 更新模式：已有记录追加 history ──
    if update_id is not None:
        with storage._lock:
            if storage._conn is None:
                return {"error": "存储引擎未初始化（无连接）"}

            # 读取旧记录
            cursor = storage._conn.execute(
                "SELECT id, content, summary FROM entries WHERE id=? AND type='knowledge'",
                (update_id,),
            )
            row = cursor.fetchone()
            if not row:
                return {"error": f"记录 #{update_id} 不存在或不是 knowledge 类型"}

            old_content = json.loads(row[1]) if isinstance(row[1], str) else row[1]
            old_fact = old_content.get("fact", "")
            old_category = old_content.get("category", "")
            old_tags = old_content.get("tags", "")

            # 构建 history 条目（只追加，不删除）
            history_entry = {
                "fact": old_fact,
                "category": old_category,
                "tags": old_tags,
                "updated_at": now,
            }
            history = old_content.get("history", [])
            history.append(history_entry)

            # 构建新 content
            new_content = {
                "fact": fact,
                "category": category,
                "tags": tags,
                "type": "fact",
                "history": history,
            }
            new_content_json = json.dumps(new_content, ensure_ascii=False)
            new_summary = f"{category}: {fact[:80]}"

            # 更新 SQLite
            storage._conn.execute(
                "UPDATE entries SET content=?, summary=?, created_at=? WHERE id=?",
                (new_content_json, new_summary, now, update_id),
            )
            storage._conn.commit()

            logger.info(
                "事实已更新: #%d | %s → %s (history: %d 版本)", update_id, old_fact[:40], fact[:40], len(history)
            )

            return {
                "result": f"已更新 #{update_id}（保留了 {len(history)} 条历史版本）",
                "id": update_id,
                "history_count": len(history),
                "updated": True,
            }

    # ── 新建模式 ──
    entry = {
        "fact": fact,
        "category": category,
        "tags": tags,
        "type": "fact",
    }

    row_id = storage.write(
        "knowledge",
        entry,
        summary=f"{category}: {fact[:80]}",
        confidence="high",
    )
    if not row_id:
        return {"error": "写入失败"}

    logger.info("事实已记住: [%s] %s", category, fact[:80])

    # --- 同步写入鉴面（失败不影响主流程）---
    try:
        from tools.mirror_tool import get_mirror_instance

        m = get_mirror_instance()
        if m:
            m.record(content=fact[:200], mtype="insight", tags=["knowledge", category], source="knowledge:remember")
    except Exception:
        logger.warning("事实已存但鉴面同步失败: %s", fact[:50], exc_info=True)

    return {
        "result": f"记住了 (#{row_id})",
        "id": row_id,
        "updated": False,
    }


# ── FTS5 搜索词自动拆分 ──

import re as _re


def _tokenize_for_fts(query: str) -> str:
    """把查询拆成 FTS5 友好的搜索词。

    FTS5 unicode61 tokenizer 对单字符中文可能无结果，
    所以策略：中文用 * 前缀搜索，英文也用 * 前缀。

    示例:
        "人类窗口 human" → "人* OR 类* OR 窗* OR 口* OR 人类窗口* OR human*"
    """
    parts = _re.findall(r"[a-zA-Z0-9_\-]+|[\u4e00-\u9fff]+", query)
    tokens = []
    for p in parts:
        if _re.match(r"^[\u4e00-\u9fff]+$", p):
            seen_chars = set()
            for ch in p:
                if ch not in seen_chars:
                    tokens.append(f"{ch}*")
                    seen_chars.add(ch)
            if len(p) > 1:
                tokens.append(f"{p}*")
        else:
            tokens.append(f"{p}*")
    return " OR ".join(tokens) if tokens else query


@tool()
async def search_knowledge(query: str, limit: int = 5) -> dict:
    """搜索已有的知识记忆。

    当你需要回忆之前记住的事实、配置、用户信息时调用。
    支持全文检索（FTS5），中文会自动拆分。

    Args:
        query: 搜索关键词，例如 "nginx"、"用户"、"端口"
        limit: 最多返回几条（默认 5，最大 20）
    """
    storage = get_global("storage")
    if not storage:
        return {"error": "存储引擎未初始化"}

    limit = min(limit, 20)
    results = []

    fts_query = _tokenize_for_fts(query)
    logger.info("知识搜索: query=%s → fts=%s", query, fts_query)

    try:
        sql_fts = (
            "SELECT id, content, summary, created_at, hits, confidence "
            "FROM entries WHERE type='knowledge' AND "
            "id IN (SELECT rowid FROM entries_fts WHERE entries_fts MATCH ?) "
            "ORDER BY hits DESC, created_at DESC LIMIT ?"
        )
        sql_like = (
            "SELECT id, content, summary, created_at, hits, confidence "
            "FROM entries WHERE type='knowledge' AND "
            "(summary LIKE ? OR content LIKE ?) "
            "ORDER BY hits DESC, created_at DESC LIMIT ?"
        )

        def _query(conn):
            try:
                rows = conn.execute(sql_fts, [fts_query, limit]).fetchall()
                if not rows:
                    logger.info("FTS 无结果，回退 LIKE 搜索")
                    rows = conn.execute(sql_like, [f"%{query}%", f"%{query}%", limit]).fetchall()
                return rows
            except Exception:
                logger.warning("FTS 异常，回退 LIKE")
                return conn.execute(sql_like, [f"%{query}%", f"%{query}%", limit]).fetchall()

        if storage._conn is not None:
            with storage._lock:
                rows = _query(storage._conn)
        else:
            import sqlite3 as _s3

            conn = _s3.connect(storage._db_path)
            try:
                rows = _query(conn)
            finally:
                conn.close()

        for r in rows:
            content = json.loads(r[1]) if isinstance(r[1], str) else r[1]
            history = content.get("history", [])
            results.append(
                {
                    "id": r[0],
                    "fact": content.get("fact", r[2]),
                    "category": content.get("category", ""),
                    "tags": content.get("tags", ""),
                    "summary": r[2],
                    "created_at": r[3],
                    "hits": r[4],
                    "confidence": r[5],
                    "history_count": len(history),
                }
            )
            if hasattr(storage, "record_hit"):
                storage.record_hit(r[0])
    except Exception as e:
        return {"error": f"搜索失败: {e}"}

    if not results:
        return {"result": "没有找到相关记忆。", "total": 0}

    lines = []
    for r in results:
        hc = r.get("history_count", 0)
        hc_str = f" [历史:{hc}版]" if hc > 0 else ""
        lines.append(f"#{r['id']} [{r['category']}]{hc_str} {r['fact'][:100]}")
        if r["tags"]:
            lines.append(f"   标签: {r['tags']}")
        dt_diff = int(time.time() - r["created_at"])
        if dt_diff < 60:
            ago = f"{dt_diff}秒前"
        elif dt_diff < 3600:
            ago = f"{dt_diff // 60}分钟前"
        elif dt_diff < 86400:
            ago = f"{dt_diff // 3600}小时前"
        else:
            ago = f"{dt_diff // 86400}天前"
        lines.append(f"   引用 {r['hits']} 次 | {ago}")
    return {"result": "找到的知识：\n" + "\n".join(lines), "total": len(results), "items": results}


@tool()
async def search_knowledge_batch(queries: str, limit: int = 3) -> dict:
    """批量搜索知识记忆。当你需要查找多个方向的信息时，用这个代替多次调用 search_knowledge。

    Args:
        queries: 逗号分隔的搜索关键词，例如 "nginx,用户,端口配置,家庭"
        limit: 每个关键词最多返回几条（默认 3，最大 5）
    """
    results = []
    qlist = [q.strip() for q in queries.split(",") if q.strip()]
    if not qlist:
        return {"result": "没有搜索关键词。", "total": 0}

    limit = min(limit, 5)
    combined = {}
    seen_ids = set()

    for q in qlist:
        try:
            r = await search_knowledge(query=q, limit=limit)
            items = r.get("items", [])
            for item in items:
                item_id = item.get("id")
                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    combined[item_id] = item
        except Exception as e:
            logger.warning("批量搜索关键词 '%s' 失败: %s", q, e)

    all_items = list(combined.values())

    if not all_items:
        return {"result": "没有找到相关记忆。", "total": 0, "items": []}

    lines = []
    for r in all_items:
        hc = r.get("history_count", 0)
        hc_str = f" [历史:{hc}版]" if hc > 0 else ""
        lines.append(f"#{r['id']} [{r.get('category', '?')}]{hc_str} {r.get('fact', '')[:80]}")
        dt_diff = int(time.time() - r.get("created_at", 0))
        if dt_diff < 60:
            ago = f"{dt_diff}秒前"
        elif dt_diff < 3600:
            ago = f"{dt_diff // 60}分钟前"
        elif dt_diff < 86400:
            ago = f"{dt_diff // 3600}小时前"
        else:
            ago = f"{dt_diff // 86400}天前"
        lines.append(f"   引用 {r.get('hits', 0)} 次 | {ago}")

    return {
        "result": f"从 {len(qlist)} 个搜索词中找到 {len(all_items)} 条知识：\n" + "\n".join(lines),
        "total": len(all_items),
        "items": all_items,
        "searched": len(qlist),
    }


@tool()
async def export_knowledge_md() -> dict:
    """导出全部 knowledge 为 Markdown 文件。

    输出路径：data/knowledge.md
    按 category 分组，包含 Timeline 历史版本。

    用途：
    - 人类可读的知识库备份
    - 可以提交到 Git 做版本管理
    - 可以作为其他 AI 工具的上下文输入
    """
    storage = get_global("storage")
    if not storage:
        return {"error": "存储引擎未初始化"}

    # 读取所有 knowledge 记录（不受 _MAX_RECORDS 限制 — 直接从 SQLite 读）
    with storage._lock:
        if storage._conn is None:
            return {"error": "存储引擎未初始化（无连接）"}

        cursor = storage._conn.execute(
            "SELECT id, content, summary, created_at, hits, confidence "
            "FROM entries WHERE type='knowledge' ORDER BY id ASC"
        )
        rows = cursor.fetchall()

    if not rows:
        return {"result": "知识库为空，无需导出。"}

    # 按 category 分组
    from collections import defaultdict

    groups = defaultdict(list)
    for r in rows:
        content = json.loads(r[1]) if isinstance(r[1], str) else r[1]
        cat = content.get("category", "general")
        groups[cat].append(
            {
                "id": r[0],
                "fact": content.get("fact", ""),
                "tags": content.get("tags", ""),
                "history": content.get("history", []),
                "summary": r[2],
                "created_at": r[3],
                "hits": r[4],
                "confidence": r[5],
            }
        )

    # 生成 Markdown
    import datetime

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# GBase 知识库",
        "",
        f"> 导出时间：{now_str}",
        f"> 总条数：{len(rows)}",
        f"> 分类数：{len(groups)}",
        "",
        "---",
        "",
    ]

    # 按 category 英文名排序，但 system/user 排前面
    cat_order = ["system", "user", "workflow", "tool", "general"]
    sorted_cats = sorted(groups.keys(), key=lambda c: (cat_order.index(c) if c in cat_order else 99, c))

    for cat in sorted_cats:
        items = groups[cat]
        lines.append(f"## {cat}（{len(items)} 条）")
        lines.append("")
        for item in items:
            dt = datetime.datetime.fromtimestamp(item["created_at"])
            dt_str = dt.strftime("%Y-%m-%d %H:%M")
            history_count = len(item["history"])
            hc_str = f" 📜{history_count}版历史" if history_count > 0 else ""

            lines.append(f"### #{item['id']} — {item['fact'][:80]}{hc_str}")
            lines.append(f"- **标签**: {item['tags'] or '（无）'}")
            lines.append(f"- **确信度**: {item['confidence']}")
            lines.append(f"- **引用**: {item['hits']} 次")
            lines.append(f"- **创建**: {dt_str}")

            if item["history"]:
                lines.append("")
                lines.append("<details>")
                lines.append(f"<summary>📜 历史版本（{history_count} 条）</summary>")
                lines.append("")
                lines.append("| # | 旧事实 | 分类 | 更新时间 |")
                lines.append("|---|--------|------|----------|")
                for hi, hv in enumerate(item["history"], 1):
                    hdt = datetime.datetime.fromtimestamp(hv["updated_at"]).strftime("%Y-%m-%d %H:%M")
                    old_fact_short = hv["fact"][:60]
                    lines.append(f"| {hi} | {old_fact_short} | {hv['category']} | {hdt} |")
                lines.append("")
                lines.append("</details>")

            lines.append("")

    md_content = "\n".join(lines)

    # 写入文件（用 write_file 会在 core 里触发备份，这里直接写）
    import os
    from pathlib import Path

    out_path = Path(storage._data_dir) / "knowledge.md"
    os.makedirs(out_path.parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    file_size = out_path.stat().st_size
    logger.info("知识库已导出: %s (%d bytes, %d 条)", out_path, file_size, len(rows))

    return {
        "result": "知识库已导出 → data/knowledge.md",
        "path": str(out_path),
        "size_bytes": file_size,
        "total": len(rows),
        "categories": len(groups),
    }
