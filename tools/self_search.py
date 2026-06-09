# SPDX-License-Identifier: MIT
"""
gbase/tools/self_search.py

Self-search: retrieve relevant records from experience/knowledge base.
Uses LLM for semantic matching, zero extra dependencies.

Process:
1. Read all experiences from storage (most recent <limit> entries)
2. Send question + experience summaries to LLM → LLM determines which are most relevant
3. Return matched experiences (up to 5)
"""

import contextlib
import logging

from lib.toolkit import get_global, tool

logger = logging.getLogger(__name__)

_SEARCH_LIMIT = 50  # Keep the most recent 50 experiences for search


@tool()
async def search_self(question: str) -> dict:
    """Search relevant information from your own experience/knowledge base.

    When you need to recall knowledge, techniques, configurations, or decisions
    from past conversations, call this tool to search your own memory.
    Especially suitable for:
    - User asks about technical configurations, historical decisions, or common settings
    - User asks something but you feel the answer may have appeared in past conversations
    - User uses natural language description (not exact keywords)

    Args:
        question: User's question or keywords
    """
    exp_engine = get_global("experience_engine")
    client = get_global("llm_client")
    model = get_global("llm_model") or "gpt-4o"

    if not exp_engine or not client:
        return {"result": "Memory system not yet ready."}

    # Read recent records (including injected=True pre-seeded experiences)
    exps = exp_engine.storage.read_recent("experience", limit=_SEARCH_LIMIT)

    if not exps:
        return {"result": "No experiences stored yet."}

    # Build search request for LLM
    candidates = []
    for i, exp in enumerate(exps):
        s = exp.get("summary", "") or ""
        c = exp.get("confidence", "low")
        if s.strip():
            candidates.append(f"{i}. [{c}] {s}")

    if not candidates:
        return {"result": "Experience base exists but has no usable summary content."}

    search_prompt = f"""You are GBase's memory search system.

The user just asked a question. You need to find the 1-3 most relevant entries from the experience base.
Note: The experiences are knowledge learned from past conversations, not what the user is asking now.

User question: "{question}"

Existing experiences (total {len(candidates)}):
{chr(10).join(candidates)}

Please return the indices of the most relevant experiences (starting from 0), one per line, output only the numbers. Return empty if none are relevant."""

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": search_prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("self_search LLM call failed: %s", e)
        return {"result": "Search failed."}

    # Parse indices
    matched = []
    lines = text.strip().split("\n")
    for line in lines:
        try:
            idx = int(line.strip())
            if 0 <= idx < len(exps):
                matched.append(exps[idx])
        except (ValueError, IndexError):
            pass

    if not matched:
        return {"result": f'Found no experience relevant to "{question}".'}

    # Record a hit
    for m in matched:
        rid = m.get("id") or m.get("rowid")
        if rid:
            with contextlib.suppress(Exception):
                exp_engine.storage.record_hit(rid)

    results = []
    for m in matched[:3]:
        s = m.get("summary", "")
        c = m.get("confidence", "low")
        results.append(f"[{c}] {s}")

    return {
        "result": "Found the following relevant experiences:\n" + "\n".join(results),
        "count": len(matched),
    }
