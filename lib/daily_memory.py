# SPDX-License-Identifier: MIT
"""
每日记忆引擎 - DailyMemoryEngine
功能：
1. 每天凌晨从 session JSONL 自动提取关键摘要 → 沉淀到 experience/knowledge
2. 启动时注入"最近重要事项"摘要到 system prompt
"""

import json
import os

# ─── 配置 ──────────────────────────────────────────
import time
from datetime import UTC, datetime

# 自动检测运行环境：优先用 gbase-home 本地路径
_CANDIDATE_DIRS = [
    os.path.join(os.path.dirname(__file__), "..", "data"),  # gbase-home/data/
    "/home/gbase-v2/data",  # GBase 云端（回退）
]

# 取第一个存在的目录
for _d in _CANDIDATE_DIRS:
    _resolved = os.path.realpath(_d)
    if os.path.isdir(_resolved):
        DATA_DIR = _resolved
        break
else:
    DATA_DIR = _CANDIDATE_DIRS[0]

DB_PATH = os.path.join(DATA_DIR, "dat.db")
MAX_INJECTION_SUMMARIES = 10  # 每次注入摘要数
SUMMARY_MIN_TOKENS = 30  # 摘要最短长度（过滤无意义短句）

# ─── 核心 ──────────────────────────────────────────


def extract_key_points_from_session(session_path: str, max_entries: int = 50) -> list[dict]:
    """
    从 session JSONL 中提取关键信息对（user question + assistant answer）。
    只保留 last N 轮对话的最后精华。
    """
    if not os.path.exists(session_path):
        return []

    with open(session_path) as f:
        lines = f.readlines()

    # 取最后 max_entries 行
    recent = lines[-max_entries:] if len(lines) > max_entries else lines

    # 提取 user/assistant/tool 对
    pairs = []
    current_q = None
    current_a = None
    for line in recent:
        try:
            d = json.loads(line)
        except Exception:
            continue
        role = d.get("role", "")
        content = d.get("content", "")
        tool_calls = d.get("tool_calls", None)

        if role == "user" and content:
            # 新问题开始，Save前一对
            if current_q and current_a:
                pairs.append({"q": current_q, "a": current_a})
            current_q = content[:200]
            current_a = None
        elif role == "assistant":
            if tool_calls and not content:
                continue  # 纯工具调用，跳过
            if content and len(content) > 10:
                current_a = content[:300]

    # 最后一对
    if current_q and current_a:
        pairs.append({"q": current_q, "a": current_a})

    return pairs


def _call_llm_summarize(q: str, a: str) -> str:
    """用 LLM 为对话对生成一句话摘要。回退到截取法。"""
    try:
        import os as _os

        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=_os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com/v1",
        )
        prompt = f"""以下是一组对话（用户问题 + AI回复）。请用一句话概括这次对话的**可重用的经验**——什么知识、决策或技巧值得在未来参考用得上。

用户: {q}
AI: {a[:500]}

一句话经验摘要（直接输出，不需要前缀）："""
        import asyncio

        resp = asyncio.wait_for(
            client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=80,
            ),
            timeout=10,
        )
        summary = resp.choices[0].message.content.strip().strip('"').strip("'")
        if len(summary) > 10 and len(summary) < 200:
            return summary
    except Exception:
        pass
    # 回退：自动构造摘要
    if len(q) < 20:
        return f"用户问 '{q}'，回复涉及 {a[:60]}..."
    return q[:80] if len(q) > 15 else f"{q}: {a[:60]}..."


def summarize_pairs_to_experience(pairs: list[dict]) -> list[dict]:
    """
    将 key points 转换成 experience 格式的摘要条目。
    用 LLM 生成可重用的经验总结，而非粗糙截取。
    """
    experiences = []
    seen = set()

    for pair in pairs:
        q = pair.get("q", "").strip()
        a = pair.get("a", "").strip()

        if not q or not a:
            continue

        if len(q) < 10:
            continue

        dedup_key = q[:60]
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # 用 LLM 生成可重用摘要
        summary = _call_llm_summarize(q, a)

        experiences.append(
            {
                "id": int(time.time() * 1000) % 1000000 + len(experiences),
                "summary": f"[会话记忆] {summary}",
                "content": json.dumps({"q": q, "a": a}, ensure_ascii=False),
                "confidence": "medium",
                "created_at": time.time(),
            }
        )

    return experiences


def inject_into_sqlite(experiences: list[dict], db_path: str):
    """将经验写入 SQLite。"""
    import sqlite3

    if not os.path.exists(db_path):
        print(f"  ❌ DB 不存在: {db_path}")
        return 0, 0

    conn = sqlite3.connect(db_path)
    existing = set()
    for row in conn.execute("SELECT summary FROM entries WHERE type='experience'"):
        existing.add(row[0])

    inserted = 0
    skipped = 0
    for exp in experiences:
        if exp["summary"] in existing:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO entries (type, summary, content, created_at, confidence, hits) VALUES (?, ?, ?, ?, ?, 0)",
            ("experience", exp["summary"], exp["content"], exp["created_at"], exp["confidence"]),
        )
        inserted += 1
        existing.add(exp["summary"])

    conn.commit()
    conn.close()
    return inserted, skipped


def get_injection_text(db_path: str = DB_PATH, limit: int = MAX_INJECTION_SUMMARIES) -> str:
    """获取最近的记忆摘要，用于注入 system prompt。"""
    import sqlite3

    if not os.path.exists(db_path):
        return ""

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT summary, confidence, created_at, hits FROM entries "
        "WHERE type='experience' AND summary LIKE '[会话记忆]%' "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        return ""

    lines = []
    for summary, confidence, created_at, _hits in rows:
        dt = datetime.fromtimestamp(created_at, tz=UTC).strftime("%m-%d %H:%M")
        lines.append(f"- [{confidence}] ({dt}) {summary}")

    return "\n\n## 📋 近期记忆（来自历史会话）\n" + "\n".join(lines)


def find_session_files(data_dir: str) -> list[str]:
    """从 data_dir/sessions/ 目录找出所有 .jsonl session 文件。"""
    sess_dir = os.path.join(data_dir, "sessions")
    if not os.path.exists(sess_dir):
        return []
    return sorted(os.path.join(sess_dir, f) for f in os.listdir(sess_dir) if f.endswith(".jsonl"))


def run_daily_extraction_for_arm(arm_name: str = "hammer", data_dir: str = None):
    """战甲专用入口：从战甲的 data_dir 扫描所有 session 并提取记忆。

    Args:
        arm_name: 战甲名称（日志用）
        data_dir: 战甲数据目录，默认 data/arms/{arm_name}/
    """
    if data_dir is None:
        data_dir = os.path.join(os.path.dirname(__file__), "..", "data", "arms", arm_name)
    db_path = os.path.join(data_dir, "dat.db") if os.path.isdir(data_dir) else DB_PATH

    session_files = find_session_files(data_dir)
    if not session_files:
        print(f"  ⚠️ [{arm_name}] 无 session 文件，跳过")
        return

    total_inserted = 0
    for sess_path in session_files:
        print(f"  [{arm_name}] 处理 session: {os.path.basename(sess_path)}")
        pairs = extract_key_points_from_session(sess_path, max_entries=50)
        experiences = summarize_pairs_to_experience(pairs)
        inserted, _ = inject_into_sqlite(experiences, db_path)
        total_inserted += inserted
    print(f"  [{arm_name}] 总计写入 {total_inserted} 条记忆")


def run_daily_extraction(session_path: str = None, db_path: str = DB_PATH):
    """入口：从 session 提取 → 沉淀到 SQLite。"""
    print(f"📥 读取 session: {session_path}")
    if not os.path.exists(session_path):
        print("  ⚠️ session 文件不存在，跳过")
        return

    pairs = extract_key_points_from_session(session_path)
    print(f"  提取到 {len(pairs)} 组对话")

    experiences = summarize_pairs_to_experience(pairs)
    print(f"  生成 {len(experiences)} 条经验摘要")

    inserted, skipped = inject_into_sqlite(experiences, db_path)
    print(f"  ✅ 写入 {inserted} 条, 跳过 {skipped} 条重复")

    # 验证
    import sqlite3

    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM entries WHERE type='experience'").fetchone()[0]
    memory_count = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE type='experience' AND summary LIKE '[会话记忆]%'"
    ).fetchone()[0]
    conn.close()
    print(f"  📊 SQLite 经验总数: {total}, 其中会话记忆: {memory_count}")
    return inserted


# ─── 跨 session Memory injection ──────────────────────────


def get_cross_session_injections(session_dir: str = None, max_recent: int = 3) -> str:
    """扫描今天的 session 文件，提取关键对话对，返回注入文本。

    作用等效于 OpenClaw 的 YF-cross-session-memory Skill：
    每次对话开始时自动把今日其他 session 的关键对话注入到 system prompt。
    不等 LLM 主动 recall。
    """
    if session_dir is None:
        session_dir = os.path.join(os.path.dirname(__file__), "..", "data", "sessions")

    if not os.path.isdir(session_dir):
        return ""

    today_sessions = []
    now = time.time()
    today_start = now - 86400  # 过去24小时
    for fname in os.listdir(session_dir):
        fpath = os.path.join(session_dir, fname)
        if not fname.endswith(".jsonl"):
            continue
        try:
            mtime = os.path.getmtime(fpath)
        except Exception:
            continue
        if mtime >= today_start:
            today_sessions.append(fpath)

    if not today_sessions:
        return ""

    snippets = []
    seen_prefixes = set()
    for sess_path in sorted(today_sessions):
        try:
            with open(sess_path) as f:
                lines = f.readlines()
        except Exception:
            continue
        # 只取最近 max_recent 轮 user↔assistant 对
        recent_lines = lines[-max_recent * 6 :] if len(lines) > max_recent * 6 else lines
        pairs = []
        current_q = None
        current_a = None
        for line in recent_lines:
            try:
                d = json.loads(line)
            except Exception:
                continue
            role = d.get("role", "")
            content = d.get("content", "") or ""
            if role == "user" and content:
                if current_q and current_a:
                    pairs.append({"q": current_q, "a": current_a})
                current_q = content[:200]
                current_a = None
            elif role == "assistant" and content and len(content) > 20:
                if not d.get("tool_calls"):
                    current_a = content[:300]
        if current_q and current_a:
            pairs.append({"q": current_q, "a": current_a})

        for p in pairs:
            q = p["q"]
            a = p["a"]
            # 质量门槛：用户问题须含问号(确实问了问题) 或 双方至少有一方 >30 字符
            is_question = "？" in q or "?" in q or "？" in q or "?" in q
            has_substance = len(q) > 30 or len(a) > 30
            if not (is_question or has_substance):
                continue
            dedup = q[:40]
            if dedup in seen_prefixes:
                continue
            seen_prefixes.add(dedup)
            snippets.append(f"- 🗣️ 问: {q} → 答: {a[:150]}")
            if len(snippets) >= 6:
                break
        if len(snippets) >= 6:
            break

    if not snippets:
        return ""

    text = "\n".join(snippets)
    return f"\n## 📜 今日其他会话（跨会话记忆）\n以下是你今天在其他会话中聊过的内容摘要，供参考：\n{text}\n"


if __name__ == "__main__":
    run_daily_extraction()
