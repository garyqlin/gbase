# SPDX-License-Identifier: MIT
"""
opprime-core-v2/tools/knowledge.py

Knowledge distillation tool — LLM decides what to remember.

remember_fact: Store a permanent fact into knowledge
search_knowledge: Full-text search existing knowledge
search_knowledge_batch: Batch search
export_knowledge_md: Export all knowledge as Markdown (human-readable)
"""

import json
import logging
import time

from lib.toolkit import get_global, tool

logger = logging.getLogger(__name__)


@tool()
async def remember_fact(fact: str, category: str = "general", tags: str = "", update_id: int = None) -> dict:
    """Remember a fact — you decide whether this is timeless information worth keeping.

    When to use:
    - User tells you their personal/family info (name, relationship, address, etc.)
    - You discover permanent system config (domain, port, path mapping, API address, etc.)
    - User uses a command/tool/idiom you didn't know before
    - You discover a fixed working pattern of a component/service
    - To update an existing record, pass update_id

    When NOT to use:
    - Time-sensitive info (weather, stock prices, news, event schedules)
    - One-off conversation content
    - Current process resource state (PID, memory usage, etc.)

    Args:
        fact: The fact description to remember, one clear and complete sentence.
        category: Classification. Options: user / system / workflow / tool / general
        tags: Comma-separated tags for easy search. e.g. "nginx,port,config"
        update_id: If updating an existing record, pass its id.
                   Old fact is auto-appended to history timeline, never deleted.
    """
    storage = get_global("storage")
    if not storage:
        return {"error": "Storage engine not initialized"}

    now = time.time()

    # ── Update mode: append history to existing record ──
    if update_id is not None:
        with storage._lock:
            if storage._conn is None:
                return {"error": "Storage engine not initialized (no connection)"}

            # Read old record
            cursor = storage._conn.execute(
                "SELECT id, content, summary FROM entries WHERE id=? AND type='knowledge'",
                (update_id,),
            )
            row = cursor.fetchone()
            if not row:
                return {"error": f"Record #{update_id} does not exist or is not a knowledge type"}

            old_content = json.loads(row[1]) if isinstance(row[1], str) else row[1]
            old_fact = old_content.get("fact", "")
            old_category = old_content.get("category", "")
            old_tags = old_content.get("tags", "")

            # Build history entry (append only, no deletion)
            history_entry = {
                "fact": old_fact,
                "category": old_category,
                "tags": old_tags,
                "updated_at": now,
            }
            history = old_content.get("history", [])
            history.append(history_entry)

            # Build new content
            new_content = {
                "fact": fact,
                "category": category,
                "tags": tags,
                "type": "fact",
                "history": history,
            }
            new_content_json = json.dumps(new_content, ensure_ascii=False)
            new_summary = f"{category}: {fact[:80]}"

            # Update SQLite
            storage._conn.execute(
                "UPDATE entries SET content=?, summary=?, created_at=? WHERE id=?",
                (new_content_json, new_summary, now, update_id),
            )
            storage._conn.commit()

            logger.info(
                "Fact updated: #%d | %s → %s (history: %d versions)", update_id, old_fact[:40], fact[:40], len(history)
            )

            return {
                "result": f"Updated #{update_id} (preserved {len(history)} historical versions)",
                "id": update_id,
                "history_count": len(history),
                "updated": True,
            }

    # ── New entry mode ──
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
        return {"error": "Write failed"}

    logger.info("Fact remembered: [%s] %s", category, fact[:80])

    # --- Sync to mirror (failure does not affect main flow) ---
    try:
        from tools.mirror_tool import get_mirror_instance

        m = get_mirror_instance()
        if m:
            m.record(content=fact[:200], mtype="insight", tags=["knowledge", category], source="knowledge:remember")
    except Exception:
        logger.warning("Fact stored but mirror sync failed: %s", fact[:50], exc_info=True)

    return {
        "result": f"Remembered (#{row_id})",
        "id": row_id,
        "updated": False,
    }


# ── FTS5 search token auto-split ──

import re as _re


def _tokenize_for_fts(query: str) -> str:
    """Split query into FTS5-friendly search tokens.

    FTS5 unicode61 tokenizer may return no results for single Chinese characters,
    so strategy: use * prefix search for Chinese, also * prefix for English.

    Example (Chinese demo — shows how CJK characters are tokenized):
        Input:  "人类窗口 human"
        Output: "人* OR 类* OR 窗* OR 口* OR 人类窗口* OR human*"
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
    """Search existing knowledge memories.

    Call when you need to recall previously remembered facts, configs, or user info.
    Supports full-text search (FTS5). Chinese is auto-tokenized.

    Args:
        query: Search keyword, e.g. "nginx", "yufei", "port"
        limit: Max results to return (default 5, max 20)
    """
    storage = get_global("storage")
    if not storage:
        return {"error": "Storage engine not initialized"}

    limit = min(limit, 20)
    results = []

    fts_query = _tokenize_for_fts(query)
    logger.info("Knowledge search: query=%s → fts=%s", query, fts_query)

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
                    logger.info("FTS no results, falling back to LIKE search")
                    rows = conn.execute(sql_like, [f"%{query}%", f"%{query}%", limit]).fetchall()
                return rows
            except Exception:
                logger.warning("FTS exception, falling back to LIKE")
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
        return {"error": f"Search failed: {e}"}

    if not results:
        return {"result": "No relevant memories found.", "total": 0}

    lines = []
    for r in results:
        hc = r.get("history_count", 0)
        hc_str = f" [history:{hc}v]" if hc > 0 else ""
        lines.append(f"#{r['id']} [{r['category']}]{hc_str} {r['fact'][:100]}")
        if r["tags"]:
            lines.append(f"   Tags: {r['tags']}")
        dt_diff = int(time.time() - r["created_at"])
        if dt_diff < 60:
            ago = f"{dt_diff}s ago"
        elif dt_diff < 3600:
            ago = f"{dt_diff // 60}m ago"
        elif dt_diff < 86400:
            ago = f"{dt_diff // 3600}h ago"
        else:
            ago = f"{dt_diff // 86400}d ago"
        lines.append(f"   Cited {r['hits']} times | {ago}")
    return {"result": "Knowledge found:\n" + "\n".join(lines), "total": len(results), "items": results}


@tool()
async def search_knowledge_batch(queries: str, limit: int = 3) -> dict:
    """Batch search knowledge memories. Use this instead of multiple search_knowledge calls
    when you need to look up multiple topics at once.

    Args:
        queries: Comma-separated search keywords, e.g. "nginx,yufei,port config,family"
        limit: Max results per keyword (default 3, max 5)
    """
    qlist = [q.strip() for q in queries.split(",") if q.strip()]
    if not qlist:
        return {"result": "No search keywords.", "total": 0}

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
            logger.warning("Batch search keyword '%s' failed: %s", q, e)

    all_items = list(combined.values())

    if not all_items:
        return {"result": "No relevant memories found.", "total": 0, "items": []}

    lines = []
    for r in all_items:
        hc = r.get("history_count", 0)
        hc_str = f" [history:{hc}v]" if hc > 0 else ""
        lines.append(f"#{r['id']} [{r.get('category', '?')}]{hc_str} {r.get('fact', '')[:80]}")
        dt_diff = int(time.time() - r.get("created_at", 0))
        if dt_diff < 60:
            ago = f"{dt_diff}s ago"
        elif dt_diff < 3600:
            ago = f"{dt_diff // 60}m ago"
        elif dt_diff < 86400:
            ago = f"{dt_diff // 3600}h ago"
        else:
            ago = f"{dt_diff // 86400}d ago"
        lines.append(f"   Cited {r.get('hits', 0)} times | {ago}")

    return {
        "result": f"Found {len(all_items)} results from {len(qlist)} search terms:\n" + "\n".join(lines),
        "total": len(all_items),
        "items": all_items,
        "searched": len(qlist),
    }


@tool()
async def export_knowledge_md() -> dict:
    """Export all knowledge as a Markdown file.

    Output path: data/knowledge.md
    Grouped by category, includes Timeline historical versions.

    Use cases:
    - Human-readable knowledge base backup
    - Can be committed to Git for version control
    - Can be used as context input for other AI tools
    """
    storage = get_global("storage")
    if not storage:
        return {"error": "Storage engine not initialized"}

    # Read all knowledge records (bypass _MAX_RECORDS — read directly from SQLite)
    with storage._lock:
        if storage._conn is None:
            return {"error": "Storage engine not initialized (no connection)"}

        cursor = storage._conn.execute(
            "SELECT id, content, summary, created_at, hits, confidence "
            "FROM entries WHERE type='knowledge' ORDER BY id ASC"
        )
        rows = cursor.fetchall()

    if not rows:
        return {"result": "Knowledge base is empty, nothing to export."}

    # Group by category
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

    # Generate Markdown
    import datetime

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Opprime Knowledge Base",
        "",
        f"> Export time: {now_str}",
        f"> Total entries: {len(rows)}",
        f"> Categories: {len(groups)}",
        "",
        "---",
        "",
    ]

    # Sort by category English name, but system/user first
    cat_order = ["system", "user", "workflow", "tool", "general"]
    sorted_cats = sorted(groups.keys(), key=lambda c: (cat_order.index(c) if c in cat_order else 99, c))

    for cat in sorted_cats:
        items = groups[cat]
        lines.append(f"## {cat} ({len(items)} entries)")
        lines.append("")
        for item in items:
            dt = datetime.datetime.fromtimestamp(item["created_at"])
            dt_str = dt.strftime("%Y-%m-%d %H:%M")
            history_count = len(item["history"])
            hc_str = f" 📜{history_count} versions" if history_count > 0 else ""

            lines.append(f"### #{item['id']} — {item['fact'][:80]}{hc_str}")
            lines.append(f"- **Tags**: {item['tags'] or '(none)'}")
            lines.append(f"- **Confidence**: {item['confidence']}")
            lines.append(f"- **Citations**: {item['hits']} times")
            lines.append(f"- **Created**: {dt_str}")

            if item["history"]:
                lines.append("")
                lines.append("<details>")
                lines.append(f"<summary>📜 Historical versions ({history_count})</summary>")
                lines.append("")
                lines.append("| # | Old fact | Category | Updated |")
                lines.append("|---|----------|----------|---------|")
                for hi, hv in enumerate(item["history"], 1):
                    hdt = datetime.datetime.fromtimestamp(hv["updated_at"]).strftime("%Y-%m-%d %H:%M")
                    old_fact_short = hv["fact"][:60]
                    lines.append(f"| {hi} | {old_fact_short} | {hv['category']} | {hdt} |")
                lines.append("")
                lines.append("</details>")

            lines.append("")

    md_content = "\n".join(lines)

    # Write file (use direct write instead of write_file to avoid backup overhead in core)
    import os
    from pathlib import Path

    out_path = Path(storage._data_dir) / "knowledge.md"
    os.makedirs(out_path.parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    file_size = out_path.stat().st_size
    logger.info("Knowledge base exported: %s (%d bytes, %d entries)", out_path, file_size, len(rows))

    return {
        "result": "Knowledge base exported → data/knowledge.md",
        "path": str(out_path),
        "size_bytes": file_size,
        "total": len(rows),
        "categories": len(groups),
    }
