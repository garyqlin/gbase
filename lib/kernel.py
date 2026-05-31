# SPDX-License-Identifier: MIT
"""
Gbase kernel loop module

Kernel Loop: LLM call → Tool execution → Next LLM call → Response.

Layer 2 of Three-Layer Architecture:
- Single responsibility: LLM invocation + tool_call execution loop
- Not responsible for: memory injection, experience storage, scout, cognitive detection
- Max 5 levels of tool call depth
"""

import asyncio
import json
import logging
import time
from collections import defaultdict

from openai import AsyncOpenAI

from tools.search import search_web

from . import toolkit
from .experience import ExperienceEngine
from .mirror import Mirror
from .session import JsonlSessionManager
from .tracer import close_trace, get_failure_analysis, init_trace, record_tool_call

# ── GMem Integration Hooks ──
# GMem is GBase's native memory system, implemented by upgrading mirror/toolkit/experience modules
# No external services, no new dependencies
# P0: KV Cache prep → hot_pattern_observe() tracks high-frequency patterns
# P1: Async memory scheduling → non-blocking experience extraction via create_task + async_record
# P2: Experience normalization → export/import version validation + filtering
# P3: Entity relationship graph → gmem_relations table + predict() multi-hop expansion


logger = logging.getLogger(__name__)


# ── GMem P1: Async background task (non-blocking) ──


async def _async_mirror_record(mirror_engine, user_message: str, reply: str, completed_ok: bool = True):
    """Background mirror recording. Empty impl — recording decision delegated to agent."""


async def _auto_note_if_deep_work(tool_count: int, reply: str, user_message: str):
    """Auto-note trigger: writes L4 note when deep work detected.

    Triggers (all must be met):
    - Tool calls >= 5 (substantial work done)
    - Reply length > 300 chars (content-rich)
    - Not a simple Q&A response

    Rationale:
    - Gundam tasks don't auto-trigger note_write on first round end
    - Hot memory (mirror) decays; deep research/design content is just fragments after restart
    - L4 notes never decay, only reliable persistence layer
    - Rather than relying on LLM to actively call note_write, the system auto-catches
    - But LLM-initiated notes (with judgment) are far better, so auto is a safety net, not replacement
    """
    # Condition 1: Sufficient tool calls
    if tool_count < 5:
        return
    # Condition 2: Sufficient reply length
    reply_len = len(reply)
    if reply_len < 300:
        return
    # Condition 3: Not trivial Q&A (probe detection)
    simple_cues = ["hello", "test", "hi", "ping", "hi_there", "hey"]
    if any(cue in user_message.strip().lower() for cue in simple_cues):
        return

    try:
        from tools.note_tool import note_write as _raw_note_write

        # Auto-generate note title
        title = (reply[:80].replace("\n", " ").strip())[:80]
        if len(title) < 5:
            title = (user_message[:60].replace("\n", " ").strip())[:60]

        # Smart content sizing (prevent excessive length)
        content = reply[:2000].strip()

        # Infer task depth from tool call count
        if tool_count >= 10:
            tags = "auto-note,deep-work,heavy"
        elif tool_count >= 7:
            tags = "auto-note,deep-work,medium"
        else:
            tags = "auto-note,deep-work,light"

        await _raw_note_write(
            title=title,
            content=f"[Auto archive] session summary\n\n## Task\n{user_message[:200]}\n\n## OutputSummary\n{content}",
            tags=tags,
            source="kernel.auto_note",
        )
        import logging as _lg

        _lg.getLogger(__name__).info("📝 Auto-note written: %s (%d chars, %d tools)", title, reply_len, tool_count)
    except Exception as e:
        import logging as _lg

        _lg.getLogger(__name__).debug("Auto-note skipped (non-blocking): %s", e)


async def _async_deep_search_save(mirror_engine, query: str, tool_name: str, _args: dict):
    """GMem P0: auto-save deep search summary to mirror."""
    try:
        summary = (query or tool_name)[:200]
        # Infer search depth from kernel file level
        mirror_engine.record_search(query, summary, depth=5)
    except Exception:
        pass


async def _async_extract_experience(
    engine,
    user_message,
    reply,
    tc_count,
    client,
    has_failure=False,
    failure_reason="",
    failed_approach="",
    dont_repeat="",
    rollback_occurred=False,
    rollback_action="",
):
    """Background experience extraction (anti-fragile: failure experience)."""
    try:
        await engine.extract(
            user_message=user_message,
            reply=reply,
            tool_calls_count=tc_count,
            has_api_error=("error" in reply.lower() if reply else False) or (tc_count >= 10),
            has_failure=has_failure,
            failure_reason=failure_reason,
            failed_approach=failed_approach,
            dont_repeat=dont_repeat,
            rollback_occurred=rollback_occurred,
            rollback_action=rollback_action,
            llm_client=client,
        )
    except Exception as e:
        logger.warning("Async experience extraction error: %s", e)


def _is_retryable_error(result: dict) -> bool:
    """Determine if tool error warrants auto-retry.
    Temporary errors (network timeout, connection failure) are retryable;
    parameter errors, permission issues are not.
    """
    err = str(result.get("error", "")).lower()
    retryable_keywords = [
        "timeout",
        "connection",
        "refused",
        "reset",
        "unreachable",
        "econnrefused",
        "econnreset",
        "eof",
    ]
    return any(kw in err for kw in retryable_keywords)


# Read from config.yaml, default 15 if not present
# Adjust via config.yaml limits.max_tool_depth
_NO_CONFIG = None
try:
    from main import _cfg_get

    _NO_CONFIG = False
except ImportError:
    _NO_CONFIG = True

if _NO_CONFIG:
    MAX_TOOL_DEPTH = 15
    # ── Circuit Breaker Configuration ──
    CIRCUIT_BREAKER = {
        "max_consecutive_failures": 10,  # Consecutive failures for same tool 10 -> 60s cooldownown
        "max_round_failures": 30,  # Total round failures 30 -> circuit breakeport
        "tool_cooldown_seconds": 30,  # Cooldown 30 seconds
        "_failures": defaultdict(int),  # {tool_name: consecutive_fail_count}
        "_round_failure_count": 0,  # cumulative failures this round
        "_cooldowns": {},  # {tool_name: unlock_timestamp}
        "_breaker_tripped": False,  # whether round breaker has tripped
    }
else:
    MAX_TOOL_DEPTH = _cfg_get("limits", "max_tool_depth", default=15)
    CIRCUIT_BREAKER = {
        "max_consecutive_failures": 2,
        "max_round_failures": 5,
        "tool_cooldown_seconds": 60,
        "_failures": defaultdict(int),
        "_round_failure_count": 0,
        "_cooldowns": {},
        "_breaker_tripped": False,
    }

"""Max tool call depth in a single run()."""

TOOL_BUDGET_WARN = 12
"""Tool call budget warning threshold. Injects reflection hint at this count."""


# ── Tool param hints (injected on tool error to help LLM fix params) ──
_tool_parameter_hints = {
    "write_file": 'Param format: {"filepath": "/path/to/file", "content": "File content"}.'
    "filepath is required, content is required. Do not send empty object {}.",
    "exec_command": 'Param format: {"command": "Command to execute"}. Required: command. Optional: workdir, timeout。',
    "read_file": 'Param format: {"filepath": "/path/to/file"}. Optional: offset, max_chars.',
}

TOOL_BUDGET_PLAN = 8
"""Above this count = complex task; suggest planning next time."""


# ── RSI Dual-Knob: Task Intent Classification ──
# This is a controlled experiment on Gundam (8440).
# Changes here affect all GBase instances in opprime/, not just Gundam.
# TODO: Ship to gbase-release after experiment validation.
_TASK_TYPES = {
    "explore": ["research", "analyze", "evaluate", "search", "compare", "proposal", "survey", "plan"],
    "execute": ["modify", "create", "deploy", "run", "startup", "install", "change", "execute", "patch"],
    "discuss": ["opinion", "view", "discuss", "suggest", "feedback", "review", "evaluate"],
    "maintain": ["check", "view", "status", "log", "fix", "diagnose", "inspect"],
}

_SHORT_EXECUTE = {"restart", "deploy", "push", "release", "rollback", "startup", "stop", "build", "rebuild"}

_TEMP_CONFIG = {
    "explore": {"mode": "warm", "mirror_max": 4, "experience_max": 2, "desc": "explore/research — light mode"},
    "execute": {"mode": "cold", "mirror_max": 6, "experience_max": 3, "desc": "modify/deploy — full mode"},
    "discuss": {"mode": "warm", "mirror_max": 3, "experience_max": 1, "desc": "discuss/feedback — minimal mode"},
    "maintain": {"mode": "cold", "mirror_max": 5, "experience_max": 2, "desc": "check/fix — focused mode"},
}


def _classify_task_intent(message: str) -> str | None:
    """Classify user message into task type.

    Returns None for short messages (< 10 chars, callers should reuse previous type).
    Returns task type string for detected messages.
    Returns "discuss" as fallback.
    """
    msg = message.strip()
    if not msg:
        return None
    # Short execute commands
    if msg in _SHORT_EXECUTE:
        return "execute"
    # Short messages reuse previous type
    if len(msg) < 10:
        return None
    # Long messages → explore
    if len(msg) > 200:
        return "explore"
    # Keyword matching
    lower = msg.lower()
    for task_type, keywords in _TASK_TYPES.items():
        for kw in keywords:
            if kw in lower:
                return task_type
    return "discuss"


class Kernel:
    """Opprime kernel."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str = "deepseek-chat",
        system_prompt: str = "You are Opprime, an intelligent assistant.",
        temperature: float = 0.7,
        max_tokens: int = 32768,
        experience_engine: ExperienceEngine | None = None,
        skill_loader=None,
        mirror_engine: Mirror | None = None,
        data_dir: str = "",
    ):
        self.client = client
        self.model = model
        self.base_system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.experience_engine = experience_engine
        self.skill_loader = skill_loader
        self.mirror_engine = mirror_engine
        self._data_dir = data_dir

        # RSI Dual-Knob: task type tracking
        self._current_task_type = "discuss"
        self._task_type_streak = 0
        # Triple-Layer Filter: current user message for intent matching
        self._current_user_message = ""

        # 🧪 Experiment #1 — OOD temperature matching
        self._ood_similarity_threshold = 0.3  # below this → unknown territory
        self._ood_exploit_threshold = 0.6  # above this → known pattern

        # 🧪 Experiment #2 — Continuous gradient accumulation
        self._gradient_log: list[dict] = []
        self._gradient_trigger_count = 5  # trigger RSI after this many entries

        # Experiment #3 — User phenotype tracking (Xinling framework)
        self._user_history: list[dict] = []  # Recent user message history
        self._user_stance = "companion"  # companion | coach
        self._trust_broken = False  # Trust broken flag
        self._trust_repair_sent = False  # Repair message sent

        # Experiment #4 — Trust breach detection
        self._user_msg_lengths: list[int] = []  # Recent N rounds message length
        self._consecutive_short = 0  # Consecutive short replies

        # ── Anti-fragile: round counter + framework introspection ──
        self._round_count: int = 0  # Cumulative dialogue rounds

        # Register global context for tool functions
        from . import toolkit as tk

        tk.set_global("llm_client", client)
        tk.set_global("llm_model", model)
        if experience_engine:
            tk.set_global("experience_engine", experience_engine)
        if mirror_engine:
            tk.set_global("mirror_engine", mirror_engine)

    def _build_dynamic_system_prompt(self) -> str:
        """Build system prompt dynamically: base identity + workspace file injection + skill index.

        Rebuilt per run() call, consistent with OpenClaw per-turn assembly.
        Assembly order follows OpenClaw buildAgentSystemPrompt + CONTEXT_FILE_ORDER.
        """
        import os
        from datetime import datetime
        from pathlib import Path

        parts = [self.base_system_prompt]

        # ── Tool list injection (compact: category tags, no schema) ──
        from .toolkit import tool_list_compact

        compact_tools = tool_list_compact()
        if compact_tools:
            parts.append(compact_tools)

        # ── Cloud: no workspace files injected (local Mac Studio only)

        # ── Skill index injection (on-demand: trigger-based match) ──
        # Only show one-line intro on no-match, no 360 full index
        if self.skill_loader:
            idx = self.skill_loader.get_skill_index()
            user_msg = (self._current_user_message or "").lower()
            if idx:
                matched = []
                for s in idx:
                    triggers = s.get("triggers", [])
                    if not triggers:
                        continue
                    for t in triggers:
                        if t.lower() in user_msg:
                            matched.append(s)
                            break
                    if len(matched) >= 5:
                        break
                if matched:
                    skill_lines = [
                        "## Available Skills (matched by trigger)",
                        "The following skills match your current task. Read their SKILL.md with `read_file` when needed.",
                        "",
                    ]
                    for s in matched:
                        loc = os.path.join(str(self.skill_loader.skills_dir), s["name"], "SKILL.md")
                        desc = s["description"][:100]
                        skill_lines.append(f"- {s['name']}: {desc}  |  location: `{loc}`")
                    skill_lines.append("")
                    skill_lines.append("(360+ skills available total \u2014 others load on demand via `read_file`)")
                    parts.append("\n".join(skill_lines))
                else:
                    parts.append(
                        "## Available Skills\n"
                        "360+ skills available. Use `read_file` to load specific SKILL.md when needed.\n"
                    )

        # ── Rule files Injected ──
        rules_dir = Path(os.getcwd()) / "rules"
        if rules_dir.is_dir():
            rule_files = sorted(rules_dir.glob("*.md"))
            if rule_files:
                rule_lines = []
                for rf in rule_files:
                    try:
                        rule_content = rf.read_text(encoding="utf-8")[:6000]
                        section_name = rf.stem.upper()
                        rule_lines.append(f"## {section_name}\n\n{rule_content}")
                    except Exception as _e:
                        logger.debug("Skip rule file %s: %s", rf.name, _e)
                if rule_lines:
                    parts.append("\n---\n".join(rule_lines))

        # ── RSI Dual-Knob: detect task type from user message ──
        temp_cfg = _TEMP_CONFIG.get(self._current_task_type, _TEMP_CONFIG["discuss"])

        # ── Mirror engine injection (layered: hot + warm memory) ──
        if self.mirror_engine:
            # L1: Hot memory — high inject_hits lesson only, max 3
            hot_text = self.mirror_engine.get_injection_text(
                max_items=3,
                ebbinghaus=True,
                user_input=self._current_user_message or "",
                tier="hot",
            )
            if hot_text:
                parts.append(hot_text)
            # L2: Warm memory — keyword-matched from recall(), max 5
            warm_text = self.mirror_engine.get_injection_text(
                max_items=5,
                ebbinghaus=True,
                user_input=self._current_user_message or "",
                tier="warm",
            )
            if warm_text:
                parts.append(warm_text)

        # ── L2 Knowledge auto-retrieval injection ──
        # At each dialogue start, match user message against knowledge base
        # Inject into system prompt on hit, LLM need not call search_knowledge
        from .toolkit import get_global

        _storage = get_global("storage")
        if _storage and self._current_user_message and len(self._current_user_message) > 3:
            try:
                _query = self._current_user_message[:200]
                logger.info("Knowledge auto-retrieval: query=%s", _query)
                # Query SQLite directly (no tool, direct storage call)
                # No word segmentation; use char-level n-gram: mono+bi-gram
                _import_re = __import__('re')
                _words = _import_re.findall(r'[a-zA-Z0-9_\-]+|[\u4e00-\u9fff]+', _query)
                _fts_tokens = []
                for _w in _words:
                    _fts_tokens.append(f"{_w}*")
                    if len(_w) > 1 and _import_re.match(r'^[\u4e00-\u9fff]+$', _w):
                        # Multi-char Chinese: split into mono-chars too
                        for _ch in _w:
                            _fts_tokens.append(f"{_ch}*")
                _fts_query = " OR ".join(_fts_tokens)[:500]
                _results = []
                with _storage._lock:
                    if _storage._conn is not None:
                        _rows = _storage._conn.execute(
                            "SELECT id, content, summary FROM entries "
                            "WHERE type='knowledge' AND "
                            "id IN (SELECT rowid FROM entries_fts WHERE entries_fts MATCH ?) "
                            "ORDER BY hits DESC, created_at DESC LIMIT 5",
                            (_fts_query,),
                        ).fetchall()
                        if not _rows:
                            # FTS no results, fallback to LIKE search
                            _rows = _storage._conn.execute(
                                "SELECT id, content, summary FROM entries "
                                "WHERE type='knowledge' "
                                "AND (summary LIKE ? OR content LIKE ?) "
                                "ORDER BY hits DESC, created_at DESC LIMIT 5",
                                (f"%{_query}%", f"%{_query}%"),
                            ).fetchall()
                        for _r in _rows:
                            try:
                                _c = json.loads(_r[1]) if isinstance(_r[1], str) else _r[1]
                                _fact = _c.get("fact", _r[2])[:200]
                                _cat = _c.get("category", "")
                                _results.append(f"  - [#{_r[0]}][{_cat}] {_fact}")
                            except Exception:
                                _results.append(f"  - [#{_r[0]}] {_r[2][:200]}")
                if _results:
                    _know_text = (
                        "\n\n## Related Knowledge (pre-loaded)\n"
                        "Knowledge facts related to your current query. "
                        "If you already know these, ignore.\n"
                        + "\n".join(_results)
                    )
                    parts.append(_know_text)
                    logger.info("Knowledge auto-retrieval: %d hits", len(_results))
                else:
                    logger.info("Knowledge auto-retrieval: no hits")
            except Exception as _e:
                logger.warning("Knowledge auto-retrieval failed (non-blocking): %s", _e)

        # ── Context handoff injection (fix AI amnesia: extract dialogue essence) ──
        if self.mirror_engine:
            handoff_text = self.mirror_engine.inject_last_context()
            if handoff_text:
                parts.append(handoff_text)

        # ── Time and timezone ──
        now = datetime.now()
        parts.append(
            f"## Current Date & Time\n"
            f"Time zone: Asia/Shanghai\n"
            f"Current time: {now.year}-{now.month}-{now.day}  {now.hour:02d}:{now.minute:02d}\n"
        )

        # ── Dynamic part (HEARTBEAT.md) after cache boundary ──
        hb_path = os.path.join(os.getcwd(), "HEARTBEAT.md")
        if os.path.isfile(hb_path):
            try:
                hb_content = Path(hb_path).read_text(encoding="utf-8")[:8000]
                parts.append(
                    "<!-- OPENCLAW_CACHE_BOUNDARY -->\n"
                    "## Dynamic Project Context\n"
                    "The following frequently-changing project context files are kept below the cache boundary when possible:\n"
                    f"## {hb_path}\n\n{hb_content}"
                )
            except Exception as _e:
                logger.debug("Heartbeat file %s read failed: %s", hb_path, _e)

        # ── Experiment #3: user relationship mode injection ──
        _rel_mode = self._user_stance
        _rel_desc = {"companion": "accompany/assist — follow user pace, dont push", "coach": "coach/inspire — challenge and provoke"}
        _trust_note = ""
        if self._trust_broken and not self._trust_repair_sent:
            _trust_note = "  |  ⚠️ Trust may be damaged: prefer gentle tone, avoid strong conclusions"
        elif self._trust_repair_sent:
            _trust_note = "  |  🛡️ Trust repair triggered: monitor if user returns to open attitude"
        parts.append(f"## Current Relation Mode\nStance: {_rel_mode} — {_rel_desc.get(_rel_mode, '')}{_trust_note}\n")

        # ── RSI Dual-Knob: run temperature injection (for LLM mode awareness) ──
        parts.append(
            f"## Current Run Mode\n"
            f"Task type: {self._current_task_type} ({temp_cfg['desc']})  |  "
            f"Mode: {temp_cfg['mode']}\n"
        )

        # ── P1: search budget guidance (inform LLM of real limit) ──
        parts.append(
            "## 🛠️ Tool Budget\n"
            f"Maximum tool call depth for this session: {MAX_TOOL_DEPTH}. "
            "Search-related tools (anysearch_search, anysearch_batch_search, anysearch_extract, "
            "honeycomb_search, search_web, fetch_page) have a dedicated budget and are not constrained "
            f"by the {MAX_TOOL_DEPTH} limit — feel free to search thoroughly. "
            "Search results are automatically persisted to memory for future reuse.\n"
        )

        # ── 🧠 Memory Warm-Up: cross-session memory forced injection ──
        # Pre-load in system prompt, not relying on LLM to recall
        _memory_injections = []
        try:
            # L0: other sessions key summaries today (cross-session equivalent)
            from .daily_memory import get_cross_session_injections
            _cross = get_cross_session_injections()
            if _cross:
                _memory_injections.append(("Other sessions today", _cross))
        except Exception:
            logger.exception("L0 cross-session memory injection failed")

        try:
            # L1: daily_memory session
            from .daily_memory import get_injection_text as daily_memory_inject
            _daily = daily_memory_inject()
            if _daily:
                _memory_injections.append(("Session memory summary", _daily))
        except Exception:
            logger.exception("L1 session memory injection failed")

        try:
            # L2: active experience injection (sorted by hits + recency filter)
            from .storage import Storage
            _st = getattr(self, "_storage_backend", None) or Storage()
            _week_ago = time.time() - 7 * 86400
            with _st._lock:
                if _st._conn is not None:
                    # Active: high-confidence anytime + mid-confidence last 7 days
                    # Active: high-confidence + mid-confidence with hits>0 + referenced in last 7d
                    _rows = _st._conn.execute(
                        "SELECT summary, created_at, hits FROM entries "
                        "WHERE type='experience' AND "
                        "(confidence='high' OR (confidence='medium' AND hits>0 AND created_at > ?)) "
                        "ORDER BY hits DESC, created_at DESC LIMIT 10",
                        (_week_ago,),
                    ).fetchall()
                    if not _rows:
                        # If no mid-confidence with hits, fallback to recent N high-confidence
                        _rows = _st._conn.execute(
                            "SELECT summary, created_at, hits FROM entries "
                            "WHERE type='experience' AND confidence='high' "
                            "ORDER BY hits DESC LIMIT 5",
                        ).fetchall()
            if _rows:
                _NOISE_PATTERNS = ["ping", "Ping", "COMPLETION SUMMARY", "connection_ok", "Packet loss"]
                _clean = []
                for _s, _ts, _h in _rows:
                    if not isinstance(_s, str):
                        continue
                    if any(_p in _s for _p in _NOISE_PATTERNS):
                        continue
                    _dt = datetime.fromtimestamp(_ts, tz=__import__('zoneinfo').ZoneInfo("Asia/Shanghai")).strftime("%m-%d")
                    _clean.append(_s[:180])
                    if len(_clean) >= 5:
                        break
                if _clean:
                    _lines = [f"  - 💡 {c}" for c in _clean]
                    _memory_injections.append(("Recent key experience", "\n".join(_lines)))

            # L2b: high-confidence knowledge injection (max 4)
            with _st._lock:
                if _st._conn is not None:
                    _kn_rows = _st._conn.execute(
                        "SELECT summary, created_at, hits FROM entries "
                        "WHERE type='knowledge' AND confidence='high' "
                        "ORDER BY hits DESC, created_at DESC LIMIT 6",
                    ).fetchall()
            if "_kn_rows" in dir() and _kn_rows:
                _lines = []
                for _s, _ts, _h in _kn_rows[:4]:
                    _dt = datetime.fromtimestamp(_ts, tz=__import__('zoneinfo').ZoneInfo("Asia/Shanghai")).strftime("%m-%d")
                    _lines.append(f"  - 💡 {_s[:180]} (hits={_h}, {_dt})")
                _memory_injections.append(("Active knowledge", "\n".join(_lines)))
        except Exception:
            logger.exception("L2 memory injection failed")

        if _memory_injections:
            # #1: Dedup — same content prefix, keep first
            _seen_prefixes = set()
            _deduped = []
            for _label, _text in _memory_injections:
                _key = _text[:200].strip()
                if _key in _seen_prefixes:
                    continue
                _seen_prefixes.add(_key)
                _deduped.append((_label, _text))
            _parts = []
            _parts.append("## History Summary\nAuto-extracted prior conversation summary for reference.\n")
            for _label, _text in _deduped:
                _parts.append(f"### {_label}\n{_text}")
            parts.append("\n".join(_parts))

        return "\n\n".join(parts)

    async def run(
        self,
        user_message: str,
        platform: str = "cli",
        session: JsonlSessionManager | None = None,
        max_seconds: int | None = None,
    ) -> str:
        """Single-turn conversation entry point.

        Flow:
        1. Build system prompt dynamically (workspace files + skill index + time)
        2. Build messages (system prompt + session context)
        3. Resolve tool list (platform + keyword routing)
        4. LLM call -> Tool loop -> Reply
        5. Background experience extraction

        Args:
            user_message: User input
            platform: Platform identifier (feishu / cli / api)
            session: Optional SessionManager

        Returns:
            Final LLM reply text
        """
        # ── GMem P0: set global ref for result auto-sedimentation ──
        if self.mirror_engine and hasattr(self.mirror_engine, "record_search"):
            toolkit.__dict__["_GMEM_MIRROR"] = self.mirror_engine

        # ── Set current user message for triple-layer intent matching ──
        self._current_user_message = user_message or ""

        # ── P2: Start async compression guard (once) ──
        if session is not None and not getattr(self, '_async_compress_started', False):
            try:
                session.start_async_compress(
                    lambda ctx: self._llm_compress_sync(ctx),
                    interval_sec=600,
                    threshold=25,
                )
            except Exception:
                logger.warning("Async compression guard start failed, non-blocking")
            self._async_compress_started = True

        # ── Build system prompt dynamically ──
        self.system_prompt = self._build_dynamic_system_prompt()

        # ── GMem P0: pre-load high-relevance memory (proactive, no tool call needed) ──
        if self.mirror_engine and len(user_message) > 3:
            predicted = self.mirror_engine.predict(user_message, top_k=5)
            if predicted:
                # Format and inject into system prompt
                mem_lines = []
                icons = {"lesson": "⚠️", "insight": "✅", "principle": "📐", "pattern": "🔄", "context": "📌"}
                for r in predicted:
                    icon = icons.get(r.get("type", ""), "📝")
                    content = r["content"][:300]
                    mem_lines.append(f"  - {icon} {content}")
                if mem_lines:
                    predicted_text = (
                        "\n```\n\n## 🔮 Predicted Memories\nMemories related to your current query (pre-loaded by GMem):\n"
                        + "\n".join(mem_lines)
                    )
                    self.system_prompt += predicted_text

        # ── Init trace ──
        import hashlib

        _trace_id = hashlib.md5((user_message + str(time.time())).encode()).hexdigest()[:12]
        init_trace(_trace_id, user_message[:100])
        _timings = [("init", time.time())]

        # ── Timing: system prompt build complete ──
        _timings.append(("build_prompt", time.time()))

        # ── RSI Dual-Knob: Update task type (only switch on 2 consecutive same) ──
        detected = _classify_task_intent(user_message)
        if detected is not None:
            if detected == self._current_task_type:
                self._task_type_streak += 1
            else:
                # New type detected — reset streak, start counting new
                self._task_type_streak = 0
                self._current_task_type = detected

        # ═══ Experimental features removed (2026-05-27) ═══
        # Removed: Exp #1 OOD, #3 stance classification, #4 trust detection
        # Kept: _classify_task_intent (task type affects mirror injection volume)
        enriched_message = user_message

        # ── 1. Skill match injection (Hermes dual-channel) ──
        # system prompt already has skill index; clear old pre-injection logic
        # Let LLM decide whether to read full SKILL.md via read_file

        _timings.append(("pre_process", time.time()))

        # ── 1.5 Search pre-execute ──
        # Auto-search when user message contains search command words
        # Note: triggers must not be too short (single char may hit normal speech)
        pre_search = False
        search_query = user_message
        search_cues = ["search", "find", "look up", "research", "google"]
        # Require: message must start or end with search intent
        has_cue = any(cue in search_query.lower() for cue in search_cues)
        starts_with_search = search_query.lower().startswith("s") and len(search_query) < 20
        if has_cue or starts_with_search:
            pre_search = True
        if pre_search:
            import re as _re

            query = _re.sub(
                r"(search|find|look up|research|google|tell me|help me find)",
                "",
                search_query,
            ).strip()
            # Add date constraint: if message has "latest/today/recent", append year-month
            time_cues = ["latest", "today", "recent", "current", "new"]
            if any(cue in search_query.lower() for cue in time_cues):
                from datetime import datetime as _dt

                now = _dt.now()
                month_str = f"{now.year}-{now.month:02d}"
                if str(now.year) not in query:
                    query = query + " " + month_str
                elif str(now.month) not in query:
                    query = query + " " + str(now.month) + "-month"
            if query:
                logger.info("Search pre-execute: query=%s", query)
                try:
                    search_result = await search_web(query=query, engines="bing_cn,duckduckgo,qwant,sogou")
                    if search_result and isinstance(search_result, dict):
                        from datetime import datetime as _dt

                        _now = _dt.now()
                        enriched_message = (
                            f"Current time is {_now.year}-{_now.month:02d}-{_now.day:02d} {_now.hour:02d}:{_now.minute:02d} (Asia/Shanghai)。\n"
                            f"\n"
                            f"[Pre-search reference] (Quick preliminary results, may not be comprehensive. Decide if further search needed)\n"
                            f"{json.dumps(search_result, ensure_ascii=False)[:4000]}\n\n"
                            f"---\n\n"
                            f"{enriched_message}"
                        )
                        logger.info("Search pre-execute complete")
                except Exception as e:
                    logger.warning("Search pre-execute failed (non-blocking): %s", e)

        # ── 2. Build messages ──
        messages: list[dict] = []
        if session:
            context = session.build_context()
            messages.extend(context)
            session.append_user_message(enriched_message)
        messages.append({"role": "user", "content": enriched_message})

        # ── Tool routing ──
        tools = toolkit.resolve_tools(platform, enriched_message)

        _timings.append(("before_llm", time.time()))

        # ── 5. First LLM call (with timeout) ──
        _loop_coro = self._loop(messages, tools, depth=0, session=session)
        timeout_happened = False
        if max_seconds:
            try:
                reply = await asyncio.wait_for(_loop_coro, timeout=max_seconds)
            except TimeoutError:
                timeout_happened = True  # noqa: F841
                reply = f"[System] Task timed out ({max_seconds}s limit)"
                logger.warning("kernel.run timeout (%ds), reply truncated", max_seconds)
        else:
            reply = await _loop_coro

        # ── 5.5 GMem memory ingestion (P2) ──
        # P1: async mirror + experience extraction + auto-note (non-blocking)
        # If reply has content, start background memory + auto-note flow
        if reply and len(reply) > 10:
            _msg = user_message
            _rep = reply
            if self.mirror_engine:
                asyncio.create_task(_async_mirror_record(self.mirror_engine, _msg, _rep))

            # Auto-note trigger: archive deep work to L4
            tc_count = len([m for m in messages if m.get("role") == "tool"])
            # Auto-note: background archive on deep work (non-blocking)
            asyncio.create_task(_auto_note_if_deep_work(tc_count, _rep, _msg))
            # 🧪 Experiment #2 — Record gradient for this turn
            self._record_gradient(user_message, reply, tc_count)
            _engine = self.experience_engine
            # 🔄 Anti-fragile: detect failure/rollback signals in reply
            _has_failure = any(kw in reply for kw in ["validation_failed", "Error", "fail", "rollback_to_baseline"])
            _failure_reason = ""
            _failed_approach = ""
            _rollback = "rollback" in reply.lower() or "roll_back" in reply
            if _has_failure:
                # Try extracting failure cause from reply
                _reply_lower = reply.lower()
                if "error" in _reply_lower:
                    _failure_reason = reply[:200]
                _failed_approach = user_message[:100]
            asyncio.create_task(
                _async_extract_experience(
                    _engine,
                    user_message,
                    reply,
                    tc_count,
                    self.client,
                    has_failure=_has_failure,
                    failure_reason=_failure_reason,
                    failed_approach=_failed_approach,
                    rollback_occurred=_rollback,
                )
            )

        # ── Anti-fragile: round count + framework introspection (path dep #68) ──
        self._round_count = getattr(self, "_round_count", 0) + 1
        if self._round_count % 50 == 0:
            _ = self._framework_self_check()

        # ── Online context compression (3-layer: L1 real-time + L2 multi-level + L3 dynamic) ──
        if session:
            _compact_interval = max(
                3, 10 - (session.get_compaction_level() if hasattr(session, "get_compaction_level") else 0)
            )
            _last_compact = getattr(self, "_last_compact_turn", 0)
            _elapsed = self._round_count - _last_compact
            _threshold = self._adaptive_compress_threshold(session)
            if session.get_stats().get("messages", 0) >= _threshold and _elapsed >= _compact_interval:
                self._last_compact_turn = self._round_count
                asyncio.create_task(self._online_compress_session(session))

        # ── 6. Close trace ──
        failure = get_failure_analysis()
        if failure and failure.get("has_failure"):
            close_trace(status="failed", error=failure["suggestion"])
        else:
            close_trace(status="completed")

        # Timing summary
        _timings.append(("total", time.time()))
        _timing_report = " | ".join(
            f"{step}: {(t - _timings[i - 1][1]) * 1000:.0f}ms" if i > 0 else f"{step}: 0ms"
            for i, (step, t) in enumerate(_timings)
        )
        logger.info("📊 Run timing: %s", _timing_report)

        return reply

    # ── 🧪 Experiment #1: OOD estimation ──
    def _estimate_ood(self, message: str) -> float | None:
        """Estimate how "known" this query is vs mirror memory.

        Returns similarity (0-1, 1=very similar to past), or None if mirror unavailable.
        """
        if not self.mirror_engine:
            return None
        try:
            msg_clean = message.strip()[:80]
            if len(msg_clean) < 5:
                return None
            # Recall top 5 similar entries from mirror
            results = self.mirror_engine.recall(msg_clean, limit=5)
            if not results:
                return 0.0  # No history at all → definitely unknown
            # Average similarity from recall results
            sims = []
            for r in results:
                if isinstance(r, dict) and "similarity" in r:
                    sims.append(r["similarity"])
                elif isinstance(r, dict) and "strength" in r:
                    sims.append(r["strength"])
            if not sims:
                return None
            return sum(sims) / len(sims)
        except Exception as e:
            logger.debug("OOD estimate failed (non-blocking): %s", e)
            return None

    # ── 🧪 Experiment #2: Gradient accumulation ──
    def _record_gradient(self, message: str, reply: str, tool_count: int):
        """Record a lightweight gradient entry for this turn.

        Accumulates until trigger count, then logs. Does not trigger real RSI
        (that's still done by evolution_engine on file change).

        # Phase 2: write RSI metrics to data/metrics/rsi_quality.jsonl
        """
        import time as _time

        entry = {
            "ts": _time.time(),
            "msg_len": len(message),
            "reply_len": len(reply),
            "tools": tool_count,
            "task_type": self._current_task_type,
            "msg_preview": message[:40],
        }
        self._gradient_log.append(entry)
        # Keep only last 50 to avoid memory bloat
        if len(self._gradient_log) > 50:
            self._gradient_log = self._gradient_log[-50:]
        if len(self._gradient_log) >= self._gradient_trigger_count:
            trigger_count = self._gradient_trigger_count
            window = self._gradient_log[-trigger_count:]
            avg_tools = sum(e["tools"] for e in window) / len(window)
            avg_msg_len = sum(e["msg_len"] for e in window) / len(window)
            task_dist = {}
            for e in window:
                task_dist[e["task_type"]] = task_dist.get(e["task_type"], 0) + 1
            dominant_task = max(task_dist, key=task_dist.get) if task_dist else "unknown"
            logger.info(
                "🧪 Gradient checkpoint: %d entries, avg_tools=%.1f, avg_msg_len=%.0f, dominant_task=%s, stance=%s",
                trigger_count,
                avg_tools,
                avg_msg_len,
                dominant_task,
                self._user_stance,
            )
            # Include user stance in checkpoint
            entry["stance"] = self._user_stance
            # Clear logged entries (keep most recent 1 to avoid edge case)
            self._gradient_log = [entry]

        # ── Phase 2: Write RSI metrics per round ──
        self._write_rsi_metric(entry)

    def _write_rsi_metric(self, entry: dict):
        """Write RSI process metrics + inference ladder log to data/metrics/ per round.

        Two data sets:
        - rsi_quality.jsonl: Quantitative metrics (tool count / task type / trust state)
        - rsi_ladder.jsonl: Inference ladder log (observation -> selection -> interpretation -> conclusion)
        """
        import json
        from pathlib import Path

        if not self._data_dir:
            return

        metrics_dir = Path(self._data_dir) / "metrics"
        metrics_dir.mkdir(parents=True, exist_ok=True)

        now_ts = entry.get("ts", time.time())

        # ── Quantitative metrics ──
        metric = {
            "ts": now_ts,
            "round": self._round_count,
            "tools": entry.get("tools", 0),
            "reply_len": entry.get("reply_len", 0),
            "task_type": entry.get("task_type", "unknown"),
            "stance": self._user_stance,
            "trust_broken": self._trust_broken,
            "verification_passed": None,
            "gradient_calls": len(self._gradient_log),
        }

        filepath = metrics_dir / "rsi_quality.jsonl"
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(metric, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("RSI metric write failed: %s", e)

        # ── Inference ladder log (ladder #81) ──
        msg = entry.get("msg_preview", "")
        tools_used = entry.get("tools", 0)
        ladder = {
            "ts": now_ts,
            "round": self._round_count,
            "steps": [
                {
                    "step": 1,
                    "action": "observe",
                    "data": f"User message: [{msg[:60]}] | Tools: {tools_used}x | Task type: {self._current_task_type}",
                },
                {
                    "step": 2,
                    "action": "select",
                    "data": (
                        "Noted: tool call density"
                        if tools_used > 5
                        else "Noted: task type classification"
                        if self._current_task_type != "discuss"
                        else "Normal dialogue flow"
                    ),
                },
                {
                    "step": 3,
                    "action": "interpret",
                    "data": (
                        f"Tool-intensive task ({tools_used}x calls)" if tools_used > 5 else f"{self._current_task_type} mode dialogue"
                    ),
                },
                {
                    "step": 4,
                    "action": "conclude",
                    "data": ("Suggest optimizing tool chain" if tools_used > 5 else "Performance stable"),
                },
            ],
        }

        ladder_path = metrics_dir / "rsi_ladder.jsonl"
        try:
            with open(ladder_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ladder, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("Inference ladder log write failed: %s", e)

    # ── Experiment #3: User phenotype classification ──
    def _update_user_stance(self, message: str):
        """Update relationship mode (companion <-> coach) based on user message features.

        Analysis dimensions:
        - Message length (long -> exploratory, short -> confirm/silent)
        - Contains challenge/question ("are you sure?", "why?" -> coach mode)
        - Contains help/follow ("help me", "how to" -> companion mode)
        """
        msg = message.strip()
        msg_lower = msg.lower()

        # Record history
        self._user_history.append({"text": msg, "length": len(msg)})
        if len(self._user_history) > 20:
            self._user_history = self._user_history[-20:]

        # Detect pattern signals
        coach_signals = [
            "为什么",
            "你确定",
            "依据",
            "来源",
            "证据",
            "真的吗",
            "不对",
            "不是",
            "why",
            "prove",
            "evidence",
        ]
        companion_signals = ["帮我", "教我", "怎么做", "能不能", "推荐", "建议", "不懂", "怎么办", "help", "how to"]

        has_coach = any(s in msg_lower for s in coach_signals)
        has_companion = any(s in msg_lower for s in companion_signals)

        # Long msg (>100 chars) + challenge -> coach mode
        if len(msg) > 100 and has_coach:
            self._user_stance = "coach"
            return

        # Short msg + help -> companion mode
        if len(msg) < 30 and has_companion:
            self._user_stance = "companion"
            return

        # Neutral; keep current mode unchanged
        # Consecutive short msgs (<15 chars) >3x -> companion
        recent = [h for h in self._user_history[-6:] if h.get("length", 0) < 15]
        if len(recent) >= 4:
            self._user_stance = "companion"

    # ── Experiment #4: Trust breach detection ──
    def _check_trust_rupture(self, message: str):
        """Detect if user trust may be damaged.

        Signals:
        - User reply length keeps shortening (was actively asking -> suddenly brief)
        - Previous round had tool errors + this round is very short
        - User switched from asking to just confirming
        """
        msg_len = len(message.strip())
        self._user_msg_lengths.append(msg_len)
        if len(self._user_msg_lengths) > 10:
            self._user_msg_lengths = self._user_msg_lengths[-10:]

        # Need at least 5 rounds for trend detection
        if len(self._user_msg_lengths) < 5:
            return

        recent = self._user_msg_lengths[-3:]
        earlier = self._user_msg_lengths[-5:-3]

        recent_avg = sum(recent) / len(recent)
        earlier_avg = sum(earlier) / len(earlier)

        # Signal 1: reply length crash (>50 to <15 chars avg)
        length_crash = earlier_avg > 50 and recent_avg < 15

        # Signal 2: 3 consecutive ultra-short (<10 chars) + prev round error
        consecutive_short = all(n < 10 for n in recent)

        if length_crash or consecutive_short:
            if not self._trust_broken:
                self._trust_broken = True
                self._trust_repair_sent = False
                logger.info(
                    "Trust breach detection: earlier_avg=%.0f -> recent_avg=%.0f (consec_short=%s)",
                    earlier_avg,
                    recent_avg,
                    consecutive_short,
                )
        else:
            # User back to normal -> clear breach flag
            if self._trust_broken and recent_avg > earlier_avg * 0.7:
                self._trust_broken = False
                self._trust_repair_sent = False
                logger.info("Trust restored")

    def _mark_repair_sent(self):
        """Mark trust repair sent, prevent duplicate."""
        self._trust_repair_sent = True
        self._trust_broken = False

    # ── Anti-fragile: external verification (Dunning-Kruger #50) ──
    def _verify_external(self, result_type: str, content: str) -> list[dict]:
        """Attempt non-LLM external verification.

        Level 1: Deterministic rules (zero cost)
        Level 2: Syntax/format checks
        Level 3: Human request (triggered at low confidence)

        Returns:
            # list of {source, passed, detail} verification results
        """
        results = []

        # Level 1: Deterministic rules
        if result_type == "code":
            # Check python syntax
            try:
                compile(content.strip(), "<verify>", "exec")
                results.append({"source": "syntax_check", "passed": True, "detail": "python syntax ok"})
            except SyntaxError as e:
                results.append({"source": "syntax_check", "passed": False, "detail": str(e)})

            # Check import legality (whitelist)
            import ast

            forbidden_imports = {"os.system", "subprocess.run", "shutil.rmtree"}
            try:
                tree = ast.parse(content.strip())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        call_str = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
                        if call_str in forbidden_imports:
                            results.append(
                                {"source": "import_check", "passed": False, "detail": f"Disallowed import: {call_str}"}
                            )
            except SyntaxError:
                pass

        elif result_type == "config":
            # Check JSON/YAML format
            for fmt_name, loader in [
                ("json", lambda s: json.loads(s)),
            ]:
                try:
                    loader(content.strip())
                    results.append({"source": fmt_name, "passed": True, "detail": f"{fmt_name} format ok"})
                    break
                except (json.JSONDecodeError, ValueError):
                    continue

        # If no auto-verification passes and content is long, flag for human
        if not results and len(content) > 200:
            results.append({"source": "human_request", "passed": None, "detail": "No auto verification available, suggest manual check"})

        return results

    # ── Anti-fragile: framework introspection (path dep #68) ──
    def _framework_self_check(self) -> dict:
        """Run every 50 rounds to check if framework-level settings need adjustment.

        Returns:
            dict with flags for potential framework issues.
        """
        report = {
            "round": self._round_count,
            "mirror_injection": 0,
            "forgetting_utility": 1.0,
            "experience_hit_rate": 0.0,
            "rollback_rate": 0.0,
            "flags": [],
        }

        # Check mirror memory injection volume
        if self.mirror_engine:
            stats = self.mirror_engine.get_stats()
            total = stats.get("total", 0)
            report["mirror_injection"] = total
            if total > 0:
                avg_strength = stats.get("avg_strength", 0)
                # If avg memory strength < 0.3 but many entries, forgetting too fast
                if avg_strength < 0.3 and total > 100:
                    report["forgetting_utility"] = 0.2
                    report["flags"].append("Forgetting too fast: avg memory strength < 0.3, may need lower decay rate")

        # Check experience hit rate and rollback rate (gradient log)
        if len(self._gradient_log) >= 10:
            window = self._gradient_log[-10:]
            total_entries = len(window)
            if total_entries > 0:
                report["experience_hit_rate"] = 1.0  # placeholder
                report["rollback_rate"] = 0.0

        if report["forgetting_utility"] < 0.3:
            logger.warning(
                "Framework introspection: forgetting mechanism degraded (forgetting_utility=%.1f), discuss framework switch",
                report["forgetting_utility"],
            )

        logger.info(
            "Framework introspection(round %d): %d memories, forgetting_utility=%.1f, flags=%s",
            self._round_count,
            report["mirror_injection"],
            report["forgetting_utility"],
            report["flags"],
        )

        return report

    def _adaptive_compress_threshold(self, session) -> int:
        """L3: Dynamic compression threshold.

        First compression: 20 messages
        After first compression: 15 (more aggressive)
        After multi-layer compression: 10 (already dense enough)
        """
        level = session.get_compaction_level() if hasattr(session, "get_compaction_level") else 0
        if level >= 2:
            return 10
        elif level >= 1:
            return 15
        return 20

    async def _fast_llm_call(self, messages: list) -> str:
        """Fast LLM call: no tools, single round, no session write."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=2048,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""

    async def _llm_compress(self, context_messages: list) -> str:
        """Generate summary via LLM (reusable by session compression layers)."""
        import httpx

        prompt = (
            "Compress the following AI assistant conversation history into a concise summary.\n"
            "Keep: user's key requests, decisions made, information gathered, tasks not completed.\n"
            "Drop: greetings, intermediate tool call details, redundant exchanges.\n"
            f"Output in plain text, under 500 characters.\n\n"
            f"Conversation:\n{json.dumps(context_messages, ensure_ascii=False)[:5000]}"
        )
        try:
            resp = httpx.post(
                str(self.client.base_url) + "chat/completions",
                headers={"Authorization": f"Bearer {self.client.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 800,
                    "temperature": 0.3,
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()[:600]
        except Exception as e:
            logger.warning("LLM compression failed: %s", e)
            return ""

    async def _online_compress_session(self, session: "JsonlSessionManager") -> None:
        """Three-layer context compression entry.

        1. L1: Online real-time compression (triggered at >= 20 messages)
        2. L2: Multi-layer summary evolution (merge when compaction exists)
        3. L3: Dynamic threshold adjustment (handled by session internally)
        """
        try:
            # L1 + L2 auto-selected by session.compress()
            await asyncio.to_thread(
                session.compress,
                lambda ctx: self._llm_compress_sync(ctx),
                15,
            )
            stats = session.get_stats()
            logger.info(
                "Context compression done (stats: %d msgs, %d compactions, level=%d)",
                stats.get("messages", 0),
                stats.get("compactions", 0),
                session.get_compaction_level(),
            )
        except Exception as e:
            logger.warning("Online compression error (non-blocking): %s", e)

    def _llm_compress_sync(self, context_messages: list) -> str:
        """Synchronous LLM compression (for asyncio.to_thread)."""
        import httpx

        kind = (
            "merge"
            if any("Summary" in (m.get("content", "") or "") for m in context_messages if m.get("role") == "user")
            else "conversation"
        )
        if kind == "merge":
            prompt = (
                "Merge the following conversation summaries into a single high-level summary.\n"
                "Keep: all decisions, completed tasks, pending items, key insights.\n"
                "Remove: overlap between summaries.\n"
                f"Output in plain text, under 600 characters.\n\n"
                f"Content:\n{json.dumps(context_messages, ensure_ascii=False)[:6000]}"
            )
        else:
            prompt = (
                "Compress the following AI assistant conversation history into a concise summary.\n"
                "Keep: user's key requests, decisions made, information gathered, tasks not completed.\n"
                "Drop: greetings, intermediate tool call details, redundant exchanges.\n"
                f"Output in plain text, under 400 characters.\n\n"
                f"Conversation:\n{json.dumps(context_messages, ensure_ascii=False)[:4000]}"
            )
        # Retry 2 times (first + 1 retry), 30s timeout each
        _last_err = None
        for _try in range(2):
            try:
                resp = httpx.post(
                    str(self.client.base_url) + "chat/completions",
                    headers={"Authorization": f"Bearer {self.client.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 800,
                        "temperature": 0.3,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()[:600]
            except Exception as e:
                _last_err = e
                if _try == 0:
                    logger.debug("LLM compression attempt 1 failed, retrying: %s", e)
                    time.sleep(1)
        logger.warning("LLM compression failed 2x: %s", _last_err)
        return ""

    async def _loop(
        self, messages: list[dict], tools: list[dict], depth: int = 0, session: JsonlSessionManager | None = None
    ) -> str:
        """Kernel recursive loop."""

        # ═══ Reset round breaker at each loop start ═══
        if depth == 0:
            CIRCUIT_BREAKER["_failures"] = defaultdict(int)
            CIRCUIT_BREAKER["_round_failure_count"] = 0
            CIRCUIT_BREAKER["_breaker_tripped"] = False
            # Clean expired cooldowns
            now = time.time()
            CIRCUIT_BREAKER["_cooldowns"] = {k: v for k, v in CIRCUIT_BREAKER["_cooldowns"].items() if v > now}

        if depth >= MAX_TOOL_DEPTH:
            # Over limit: let LLM summarize from collected info
            logger.info("Reached max tool depth %s, summarizing from info", MAX_TOOL_DEPTH)
            final_response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    *messages,
                    {
                        "role": "user",
                        "content": "Based on the collected info, answer the original question. If info insufficient, state what was found and what was not, do not ask user what to do next.",
                    },
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            choice0 = final_response.choices[0]
            reply = choice0.message.content or ""
            finish_reason = getattr(choice0, "finish_reason", None)
            if finish_reason == "length":
                logger.warning("LLM output truncated (finish_reason=length)! max_tokens=%s may not be enough", self.max_tokens)
                reply += "\n\n[Output truncated, results may be incomplete]"
            if session:
                session.append({"role": "assistant", "content": reply})
            return reply

        # Call LLM
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": self.system_prompt}] + messages,
            tools=tools if tools else None,
            tool_choice="auto" if tools else None,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        choice = response.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            logger.warning("LLM output truncated (finish_reason=length)! max_tokens=%s may not be enough", self.max_tokens)
        msg = choice.message

        # DeepSeek V4 reasoning model must return reasoning_content
        _reasoning = None
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            _reasoning = msg.reasoning_content
        elif hasattr(msg, "model_extra") and msg.model_extra:
            _reasoning = msg.model_extra.get("reasoning_content")

        # ── Plain text reply ──
        if not msg.tool_calls:
            content = msg.content or ""
            asst = {"role": "assistant", "content": content}
            if _reasoning:
                asst["reasoning_content"] = _reasoning
            if session:
                session.append(asst)
            return content

        # ── Has tool_calls ──
        assistant_msg = {"role": "assistant"}
        if _reasoning:
            assistant_msg["reasoning_content"] = _reasoning
        if msg.content:
            assistant_msg["content"] = msg.content
        assistant_tool_calls = []
        for tc in msg.tool_calls:
            assistant_tool_calls.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
            )
        assistant_msg["tool_calls"] = assistant_tool_calls
        messages.append(assistant_msg)
        if session:
            session.append(assistant_msg)

        # Parallel tool execution (independent tools at same level)
        async def _run_one_tool(tc):
            """Execute single tool and return tool message.

            Built-in circuit breaker:
            - Same tool fails 2x consecutive -> 60s cooldown
            - Total round failures 5x -> circuit break report
            """
            func_name = tc.function.name
            try:
                func_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                func_args = {}

            # ═══ Breaker: check if tool in cooldown ═══
            now = time.time()
            cooldown_until = CIRCUIT_BREAKER["_cooldowns"].get(func_name, 0)

            if now < cooldown_until:
                remaining = int(cooldown_until - now)
                logger.info("Tool %s in cooldown (remaining %ds), skip", func_name, remaining)
                return {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"[Breaker] Tool {func_name} hit failure limit, cooldown {remaining}s. Try other tools (e.g. exec_safe('cat <path>') instead of read_file) or wait.",
                }

            # ═══ Breaker: check if round is tripped ═══
            if CIRCUIT_BREAKER["_breaker_tripped"]:
                logger.info("Round breaker tripped, tool %s skipped", func_name)
                return {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "[Breaker] Round terminated due to consecutive failures. Options: use other tools/approach, fix and wait for cooldown, or wait 60s for auto-reset.",
                }

            logger.info("Tool call: %s(%s)", func_name, json.dumps(func_args, ensure_ascii=False)[:120])

            # Auto-retry (max 1)
            _trace_start = time.time()
            result = None
            for _retry in range(2):
                result = await toolkit.execute(func_name, func_args)
                if isinstance(result, dict) and _is_retryable_error(result):
                    logger.warning("Tool %s returned error, auto-retry: %s", func_name, str(result.get("error", ""))[:200])
                    continue
                break
            _trace_elapsed = (time.time() - _trace_start) * 1000

            result_str = json.dumps(result, ensure_ascii=False)
            logger.info("Tool %s returned: %s chars, first300=%s", func_name, len(result_str), result_str[:300].replace("\n", " "))

            # ── Error detection ──
            has_error = "error" in result if isinstance(result, dict) else False

            # ── Trace recording (after has_error determined) ──
            record_tool_call(
                step=len([m for m in messages if m.get("role") == "assistant"]),
                tool_name=func_name,
                input_digest=json.dumps(func_args, ensure_ascii=False)[:200],
                output_digest=result_str[:200],
                status="error" if has_error else "ok",
                error=str(result.get("error", "")) if has_error else "",
                duration_ms=_trace_elapsed,
            )

            # ── Error hint injection ──
            if has_error and func_name in _tool_parameter_hints:
                err_text = str(result.get("error", ""))
                hint = _tool_parameter_hints[func_name]
                if hint not in err_text:
                    logger.info("Injected param hint into error message")
                    result["error"] = f"{err_text}\n\n[Param hint] {hint}"
                    result_str = json.dumps(result, ensure_ascii=False)

            # ═══ Breaker: update concurrent failure counter ═══
            has_error = "error" in result if isinstance(result, dict) else False
            if has_error:
                CIRCUIT_BREAKER["_failures"][func_name] += 1
                CIRCUIT_BREAKER["_round_failure_count"] += 1

                consecutive = CIRCUIT_BREAKER["_failures"][func_name]
                if consecutive >= CIRCUIT_BREAKER["max_consecutive_failures"]:
                    cooldown = CIRCUIT_BREAKER["tool_cooldown_seconds"]
                    CIRCUIT_BREAKER["_cooldowns"][func_name] = time.time() + cooldown
                    CIRCUIT_BREAKER["_failures"][func_name] = 0  # Reset; cooldown doesnt count
                    logger.warning("Tool %s failed %d consecutive times, cooldown %ds", func_name, consecutive, cooldown)
                if CIRCUIT_BREAKER["_round_failure_count"] >= CIRCUIT_BREAKER["max_round_failures"]:
                    CIRCUIT_BREAKER["_breaker_tripped"] = True
                    logger.warning("Breaker tripped! %d cumulative failures", CIRCUIT_BREAKER["_round_failure_count"])
                    result["_breaker_tripped"] = True  # Flag in result
            else:
                # On success, reset tool failure count
                CIRCUIT_BREAKER["_failures"][func_name] = 0
            return {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str[:5000],
            }

        # ── Tool result aggregation + recursion ──
        tool_results = await asyncio.gather(*[asyncio.create_task(_run_one_tool(tc)) for tc in msg.tool_calls])
        for tr in tool_results:
            if tr:
                messages.append(tr)
                if session:
                    session.append(tr)

        # Max recursion depth: 15
        if depth + 1 >= MAX_TOOL_DEPTH:
            return await self._loop(messages, tools, depth=depth + 1, session=session)
        return await self._loop(messages, tools, depth=depth + 1, session=session)
