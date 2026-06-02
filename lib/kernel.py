# SPDX-License-Identifier: MIT
"""
gbase/lib/kernel.py

Kernel loop: LLM invocation → tool execution → recall → response.

Layer 2 of the three-tier architecture:
- - Single responsibility: LLM call + tool_call execution loop
- Not responsible for: Memory injection, experience storage, scout, cognitive detection
- - Maximum 5 levels of tool call depth
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

# ── GMem Integration Hook ──
# # GMem is GBase's built-in memory system, implemented by upgrading three submodules: mirror / toolkit / experience
# # No external service dependencies, no new dependencies introduced
# # P0: KV Cache preparation → hot_pattern_observe() tracks high-frequency patterns
# P1: Asynchronous memory scheduling → create_task non-blocking Experience extraction + async_record
# # P2: Experience standardization → export/import version verification + filtering
# # P3: Entity relationship graph → gmem_relations table + predict() multi-hop extension


logger = logging.getLogger(__name__)


# ── GMem P1: Asynchronous background tasks (non-blocking main thread) ──


async def _async_mirror_record(mirror_engine, user_message: str, reply: str, completed_ok: bool = True):
    """Record mirror memory in background. Empty implementation - recording decision is entirely delegated to the agent."""


async def _auto_note_if_deep_work(tool_count: int, reply: str, user_message: str):
    """Auto note trigger: Write to L4 note in background when deep work is detected.

    Trigger conditions (all must be met):
    - Tool calls >= 5 (indicates substantial work done)
    - IP reply length > 300 characters (indicates substantial content)
    - Not a simple reply (no pure Q不是简单回复（不含纯问答特征）A features)

    Rationale:
    - Gundam tasks do not automatically trigger note_write at the end of a round
    - Hot memory mirror will decay, content from deep research/design will only be fragments after restart
    - L4 notes do not decay, is the only reliable persistence layer
    - Instead of relying on LLM to remember to call note_write actively, let the system automatically cover the bottom line
    - But LLM actively written (with judgment) is far better than automatic ones, so auto only serves as a bottom line, does not replace manual writing
    """
    # Condition 1: Tool call count meets threshold
    if tool_count < 5:
        return
    # Condition 2: Response content is substantial
    reply_len = len(reply)
    if reply_len < 300:
        return
    # Condition 3: Not pure simple Q&A (probe)
    simple_cues = ["你好", "测试", "嗨", "在吗", "hi", "hello", "ping", "测试一下", "早安", "晚安"]
    if any(cue in user_message.strip().lower() for cue in simple_cues):
        return

    try:
        from tools.note_tool import note_write as _raw_note_write

        # Auto generate note title
        title = (reply[:80].replace("\n", " ").strip())[:80]
        if len(title) < 5:
            title = (user_message[:60].replace("\n", " ").strip())[:60]

        # Smart estimate note content (prevent exceeding reasonable length)
        content = reply[:2000].strip()

        # Estimate task depth from tool call count
        if tool_count >= 10:
            tags = "auto-note,deep-work,heavy"
        elif tool_count >= 7:
            tags = "auto-note,deep-work,medium"
        else:
            tags = "auto-note,deep-work,light"

        await _raw_note_write(
            title=title,
            content=f"[System Auto Archive] From conversation summary\n\n## Current Task\n{user_message[:200]}\n\n## Output Summary\n{content}",
            tags=tags,
            source="kernel.auto_note",
        )
        import logging as _lg

        _lg.getLogger(__name__).info("📝 Auto-note written: %s (%d chars, %d tools)", title, reply_len, tool_count)
    except Exception as e:
        import logging as _lg

        _lg.getLogger(__name__).debug("Auto-note skipped (non-blocking): %s", e)


async def _async_deep_search_save(mirror_engine, query: str, tool_name: str, _args: dict):
    """GMem P0: Automatically save search result summary to mirror after deep search."""
    try:
        summary = (query or tool_name)[:200]
        # Estimate search depth from kernel file hierarchy
        mirror_engine.record_search(query, summary, depth=5)
    except Exception as _e:
        logger.debug("GMem P0 search summary recording failed: %s", _e)


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
    """Extract experience in background (includes anti-fragility: failure experience)."""
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
        logger.warning("异步Experience extraction异常: %s", e)


def _is_retryable_error(result: dict) -> bool:
    """判断工具返回的错误是否值得自动重试。
    网络超时、连接失败等临时性错误可重试；
    参数错误、权限不足等不可重试。
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


# Read from config.yaml, default 15 if not exists
# Configurable通过修改 config.yaml limits.max_tool_depth 调整
_NO_CONFIG = None
try:
    from main import _cfg_get

    _NO_CONFIG = False
except ImportError:
    _NO_CONFIG = True

if _NO_CONFIG:
    MAX_TOOL_DEPTH = 15
    # ── 工具熔断器配置 ──
    CIRCUIT_BREAKER = {
        "max_consecutive_failures": 3,  # 同工具连续失败 3 次 → 暂停（反脆弱：快速熔断）
        "max_round_failures": 10,  # 整轮失败 10 次 → 熔断上报
        "tool_cooldown_seconds": 30,  # 基础冷却 30 秒（指数退避：30→60→120→240→480→600 封顶）
        "tool_cooldown_max": 600,  # 冷却封顶 10 分钟
        "_failures": defaultdict(int),  # {tool_name: consecutive_fail_count}
        "_round_failure_count": 0,  # 本轮累计失败数
        "_cooldowns": {},  # {tool_name: unlock_timestamp}
        "_cooldown_attempts": defaultdict(int),  # {tool_name: 冷却次数（用于指数退避）}
        "_breaker_tripped": False,  # 整轮熔断是否已触发
    }

    # ── 工具备用路径映射（反脆弱：工具失败时自动切换） ──
    FALLBACK_MAP = {
        # 文件读写
        "read_file": ["exec_safe"],
        "write_file": ["exec_safe"],
        "validate_file": ["exec_safe"],
        # 搜索（按优先级排列）
        "anysearch_search": ["honeycomb_search", "search_web"],
        "anysearch_batch_search": ["honeycomb_search", "search_web"],
        "anysearch_extract": ["fetch_page"],
        "honeycomb_search": ["search_web", "anysearch_search"],
        "search_web": ["honeycomb_search", "anysearch_search"],
        "fetch_page": ["anysearch_extract"],
        # 执行
        "exec_command": ["exec_safe"],
        "exec_safe": ["exec_command"],
        # 代码生成
        "cc_execute": ["exec_safe"],
        # 笔记
        "note_write": ["exec_safe"],
        "note_search": ["exec_safe"],
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

"""单次 run() 中最多允许的工具调用层数。"""

TOOL_BUDGET_WARN = 12
"""工具调用预算警告线。达到此数时注入反思提示。"""


# ── 工具参数提示（工具错误时注入，帮助 LLM 修正参数） ──
_tool_parameter_hints = {
    "write_file": '参数格式: {"filepath": "/path/to/file", "content": "文件内容"}。'
    "filepath 是必填文件路径，content 是必填文件内容。不要传空对象 {}。",
    "exec_command": '参数格式: {"command": "要执行的命令"}。command 是必填字符串。Configurable选参数: workdir, timeout（文件扫描/桌面操作建议 30-60 秒，简单命令 5-15 秒）。',
    "read_file": '参数格式: {"filepath": "/path/to/file"}。Configurable选参数: offset, max_chars。',
}

TOOL_BUDGET_PLAN = 8
"""超过此数视为复杂任务，下次同类任务应建议先规划。"""


# ── RSI Dual-Knob: Task Intent Classification ──
# This is a controlled experiment on Gundam (8440).
# Changes here affect all GBase instances in gbase/, not just Gundam.
# TODO: Ship to gbase-release after experiment validation.
_TASK_TYPES = {
    "explore": ["研究", "分析", "评估", "搜索", "对比", "方案", "proposal", "survey", "调研"],
    "execute": ["修改", "创建", "部署", "运行", "启动", "安装", "改", "执行", "添加", "删除"],
    "discuss": ["你认为", "怎么看", "讨论", "建议", "意见", "反馈", "看法", "评价"],
    "maintain": ["检查", "查看", "状态", "日志", "修复", "排查", "看下", "诊断"],
}

_SHORT_EXECUTE = {"重启", "部署", "推送", "发布", "Rollback", "启动", "停止", "构建", "还原"}

_TEMP_CONFIG = {
    "explore": {"mode": "warm", "mirror_max": 4, "experience_max": 2, "desc": "探索/研究 — 轻量模式"},
    "execute": {"mode": "cold", "mirror_max": 6, "experience_max": 3, "desc": "修改/部署 — 专注模式"},
    "discuss": {"mode": "warm", "mirror_max": 3, "experience_max": 1, "desc": "讨论/反馈 — 精简模式"},
    "maintain": {"mode": "cold", "mirror_max": 5, "experience_max": 2, "desc": "检查/修复 — 聚焦模式"},
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
    """GBase 内核。"""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str = "deepseek-chat",
        system_prompt: str = "你是 GBase,一个智能助手。",
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

        # 🧪 Experiment #3 — 用户表型追踪（心凌框架）
        self._user_history: list[dict] = []  # 最近用户消息历史
        self._user_stance = "companion"  # companion | coach
        self._trust_broken = False  # 信任破裂标记
        self._trust_repair_sent = False  # 是否已发送修复消息

        # 🧪 Experiment #4 — 信任破裂检测
        self._user_msg_lengths: list[int] = []  # 最近 N 轮用户消息长度
        self._consecutive_short = 0  # 连续简短回复计数

        # ── Anti-fragile: loop counting + 框架自省 ──
        self._round_count: int = 0  # 累加对话轮次

        # ── ArchiveStore 初始化（无 session 依赖，全局写入 + 全局Search） ──
        from pathlib import Path
        self._archive_store = None
        if data_dir:
            try:
                from .archive_store import ArchiveStore
                _archive_db_path = Path(data_dir) / "archive.db"
                self._archive_store = ArchiveStore(session_key="global", db_path=_archive_db_path)
                logger.info("ArchiveStore 初始化完成, db_path=%s", _archive_db_path)
            except Exception as e:
                logger.warning("ArchiveStore 初始化失败: %s", e)

        # 注册全局上下文供工具函数读取
        from . import toolkit as tk

        tk.set_global("llm_client", client)
        tk.set_global("llm_model", model)
        if experience_engine:
            tk.set_global("experience_engine", experience_engine)
        if mirror_engine:
            tk.set_global("mirror_engine", mirror_engine)

    def _build_dynamic_system_prompt(self) -> str:
        """Dynamically build system prompt：基础身份 + workspace file injection + skill 索引。

        每次 run() 调用时重建，与 OpenClaw 每 turn 重新拼装的逻辑一致。
        拼装顺序参考 OpenClaw 的 buildAgentSystemPrompt + CONTEXT_FILE_ORDER。
        """
        import os
        from datetime import datetime
        from pathlib import Path

        parts = [self.base_system_prompt]

        # ── 工具列表注入（精简版：分类标签，不展开 schema） ──
        from .toolkit import tool_list_compact

        compact_tools = tool_list_compact()
        if compact_tools:
            parts.append(compact_tools)

        # ── 云端：无 workspace 文件注入（这些文件只在本地 Mac Studio）

        # ── Skill Router（SkillRouter + SkillLoader 双层匹配） ──
        if self.skill_loader:
            from .skill_router import SkillRouter
            router = SkillRouter(
                self.skill_loader,
                os.path.join(os.getcwd(), "skills-index.json"),
            )
            user_msg = (self._current_user_message or "")
            route_result = router.get_route_instruction(user_msg, inject_lines=20)
            if route_result:
                parts.append(route_result)
            else:
                parts.append(
                    "## Available Skills\n"
                    "360+ skills available. Use `read_file` to load specific SKILL.md when needed.\n"
                )

        # ── Rule files 注入 ──
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
                        logger.debug("跳过规则文件 %s: %s", rf.name, _e)
                if rule_lines:
                    parts.append("\n---\n".join(rule_lines))

        # ── RSI Dual-Knob: Run Temperature — 使用用户消息判断任务类型 ──
        temp_cfg = _TEMP_CONFIG.get(self._current_task_type, _TEMP_CONFIG["discuss"])

        # ── Mirror Engine注入（分层：热记忆 + 温记忆） ──
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

        # ── L2 Knowledge 自动Search注入 ──
        # 每次对话启动时，用当前用户消息匹配知识库中的事实
        # 命中后注入 system prompt，不依赖 LLM 自己记得去 search_knowledge
        from .toolkit import get_global

        _storage = get_global("storage")
        if _storage and self._current_user_message and len(self._current_user_message) > 3:
            try:
                _query = self._current_user_message[:200]
                logger.info("Knowledge 自动Search: query=%s", _query)
                # 直接查 SQLite (不走 tool, 直接调 storage)
                # 中文不分词，改用字符级 n-gram: 单字+双字组合
                _import_re = __import__('re')
                _words = _import_re.findall(r'[a-zA-Z0-9_\-]+|[\u4e00-\u9fff]+', _query)
                _fts_tokens = []
                for _w in _words:
                    _fts_tokens.append(f"{_w}*")
                    if len(_w) > 1 and _import_re.match(r'^[\u4e00-\u9fff]+$', _w):
                        # 中文多字词，拆单字也加进去
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
                            # FTS 无结果，回退 LIKE 搜索
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
                            except Exception as _parse_e:
                                logger.debug("知识解析失败 (id=%s): %s", _r[0], _parse_e)
                                _results.append(f"  - [#{_r[0]}] {_r[2][:200]}")
                if _results:
                    _know_text = (
                        "\n\n## Related Knowledge (pre-loaded)\n"
                        "Knowledge facts related to your current query. "
                        "If you already know these, ignore.\n"
                        + "\n".join(_results)
                    )
                    parts.append(_know_text)
                    logger.info("Knowledge 自动Search: 命中 %d 条", len(_results))
                else:
                    logger.info("Knowledge 自动Search: 无命中")
            except Exception as _e:
                logger.warning("Knowledge 自动Search失败（Non-blocking for main flow）: %s", _e)

        # ── 上下文交接注入（修复 AI 失忆：Extract conversation essence from previous session） ──
        if self.mirror_engine:
            handoff_text = self.mirror_engine.inject_last_context()
            if handoff_text:
                parts.append(handoff_text)

        # ── 时间时区 ──
        now = datetime.now()
        parts.append(
            f"## Current Date & Time\n"
            f"Time zone: Asia/Shanghai\n"
            f"Current time: {now.year}年{now.month}月{now.day}日 {now.hour:02d}:{now.minute:02d}\n"
        )

        # ── 动态部分（HEARTBEAT.md）放到 cache boundary 之后 ──
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
                logger.debug("心跳文件 %s 读取失败: %s", hb_path, _e)

        # ── 🧪 Experiment #3: 用户关系模式注入 ──
        _rel_mode = self._user_stance
        _rel_desc = {"companion": "陪伴/辅助 — 以用户节奏为准，不强推观点", "coach": "教练/启发 — 适度挑战和反问"}
        _trust_note = ""
        if self._trust_broken and not self._trust_repair_sent:
            _trust_note = "  |  ⚠️ 信任Configurable能受损：优先使用温和语气，避免强势结论"
        elif self._trust_repair_sent:
            _trust_note = "  |  🛡️ 信任修复已触发：持续关注用户是否恢复开放态度"
        parts.append(f"## Current Relation Mode\nStance: {_rel_mode} — {_rel_desc.get(_rel_mode, '')}{_trust_note}\n")

        # ── RSI Dual-Knob: 运行温度注入（用于 LLM 感知当前模式） ──
        parts.append(
            f"## Current Run Mode\n"
            f"Task type: {self._current_task_type} ({temp_cfg['desc']})  |  "
            f"Mode: {temp_cfg['mode']}\n"
        )

        # ── P1: 搜索预算指引（告知 LLM 真实调用上限） ──
        parts.append(
            "## 🛠️ Tool Budget\n"
            f"Maximum tool call depth for this session: {MAX_TOOL_DEPTH}. "
            "Search-related tools (anysearch_search, anysearch_batch_search, anysearch_extract, "
            "honeycomb_search, search_web, fetch_page) have a dedicated budget and are not constrained "
            f"by the {MAX_TOOL_DEPTH} limit — feel free to search thoroughly. "
            "Search results are automatically persisted to memory for future reuse.\n"
        )

        # ── 🧠 Memory Warm-Up: 跨会话记忆强制注入 ──
        # 不依赖 LLM 主动调用 recall，在 system prompt 里强行加载
        _memory_injections = []
        try:
            # L0: 今天其他 session 的关键摘要（跨会话记忆，等效 cross-session skill）
            from .daily_memory import get_cross_session_injections
            _cross = get_cross_session_injections()
            if _cross:
                _memory_injections.append(("今日其他会话", _cross))
        except Exception:
            logger.exception("L0 跨会话Memory injection失败")

        try:
            # L1: daily_memory 会话记忆
            from .daily_memory import get_injection_text as daily_memory_inject
            _daily = daily_memory_inject()
            if _daily:
                _memory_injections.append(("会话记忆摘要", _daily))
        except Exception:
            logger.exception("L1 会话Memory injection失败")

        try:
            # L2: 活跃Experience injection（按 hits 排序 + 最近7天中置信度过滤）
            _rows = []
            _kn_rows = []
            from .storage import Storage
            _st = getattr(self, "_storage_backend", None)
            if _st is None:
                _st = Storage()
            _week_ago = time.time() - 7 * 86400
            with _st._lock:
                if _st._conn is not None:
                    # 活跃记忆: 高置信度不限时间 + 中置信度最近7天
                    # 活跃记忆: 高置信度 + hits>0 的中置信度 + 最近7天被引用过
                    _rows = _st._conn.execute(
                        "SELECT summary, created_at, hits FROM entries "
                        "WHERE type='experience' AND "
                        "(confidence='high' OR (confidence='medium' AND hits>0 AND created_at > ?)) "
                        "ORDER BY hits DESC, created_at DESC LIMIT 10",
                        (_week_ago,),
                    ).fetchall()
                    if not _rows:
                        # 如果无人问津过的中置信度也没有，回退到最近N条high
                        _rows = _st._conn.execute(
                            "SELECT summary, created_at, hits FROM entries "
                            "WHERE type='experience' AND confidence='high' "
                            "ORDER BY hits DESC LIMIT 5",
                        ).fetchall()
            if _rows:
                _NOISE_PATTERNS = ["ping", "Ping", "COMPLETION SUMMARY", "连接验证成功", "Packet loss"]
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
                    _memory_injections.append(("近期关键经验", "\n".join(_lines)))

            # L2b: 高置信度 knowledge 注入（知识类，最多 4 条）
            _kn_rows = []
            with _st._lock:
                if _st._conn is not None:
                    _kn_rows = _st._conn.execute(
                        "SELECT summary, created_at, hits FROM entries "
                        "WHERE type='knowledge' AND confidence='high' "
                        "ORDER BY hits DESC, created_at DESC LIMIT 6",
                    ).fetchall()
            _kn_rows = _kn_rows or []  # 保护: fetchall Configurable能返回 None
            if _kn_rows:
                _lines = []
                for _s, _ts, _h in _kn_rows[:4]:
                    if not isinstance(_s, str):
                        continue
                    _dt = datetime.fromtimestamp(_ts, tz=__import__('zoneinfo').ZoneInfo("Asia/Shanghai")).strftime("%m-%d")
                    _lines.append(f"  - 💡 {_s[:180]} (hits={_h}, {_dt})")
                _memory_injections.append(("活跃知识点", "\n".join(_lines)))
        except Exception:
            logger.exception("L2 Memory injection失败")

        if _memory_injections:
            # #1: 去重 — 相同内容前缀只保留第一条
            _seen_prefixes = set()
            _deduped = []
            for _label, _text in _memory_injections:
                _key = _text[:200].strip()
                if _key in _seen_prefixes:
                    continue
                _seen_prefixes.add(_key)
                _deduped.append((_label, _text))
            _parts = []
            _parts.append("## 📜 历史记录摘要\n以下是系统自动提取的过往历史记录摘要，用于辅助参考。注意这些不是当前对话内容，而是之前发生过的事情的记录。请区分使用。\n")
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
        """单次对话入口。

        流程:
        1. 动态拼装 system prompt（workspace files + skill 索引 + 时间）
        2. 构建 messages（含 system prompt + session context）
        3. 解析工具列表（平台 + 关键词路由）
        4. 调用 LLM → 工具循环 → 回复
        5. 后台提取经验

        Args:
            user_message: 用户输入
            platform: 平台标识（feishu / cli / api）
            session: 可选的 SessionManager

        Returns:
            LLM 最终回复文本
        """
        # ── GMem P0: 设置搜索结果Auto sedimentation的全局引用 ──
        if self.mirror_engine and hasattr(self.mirror_engine, "record_search"):
            toolkit.__dict__["_GMEM_MIRROR"] = self.mirror_engine

        # ── Set current user message for triple-layer intent matching ──
        self._current_user_message = user_message or ""

        # ── ArchiveStore Search（跨 session 搜索） ──
        archive_hits = ""
        if self._archive_store and len(user_message) > 3:
            try:
                # 全局搜索：不限制 session_key
                import sqlite3 as _sqlite3
                _db_path = self._archive_store.db_path
                _keywords = self._archive_store._extract_keywords(user_message)
                if _keywords:
                    _kw_conds = " OR ".join(f"content LIKE ? COLLATE NOCASE" for _ in _keywords)
                    _sql = f"""
                        SELECT content, role, timestamp FROM archive_entries
                        WHERE {_kw_conds}
                        ORDER BY timestamp DESC LIMIT 8
                    """
                    _params = [f"%{k}%" for k in _keywords]
                    _conn = _sqlite3.connect(_db_path)
                    try:
                        _rows = _conn.execute(_sql, _params).fetchall()
                    finally:
                        _conn.close()
                    archive_lines = []
                    for _content, _role, _ts in _rows:
                        if not _content:
                            continue
                        # 简单去重：相同 content 不重复显示
                        _content_str = _content[:400]
                        _ts_str = time.strftime("%m-%d %H:%M", time.localtime(_ts)) if _ts else ""
                        _who = "我" if _role == "user" else "你"
                        archive_lines.append(f"  [{_ts_str}] ({_who}) {_content_str}")
                    if archive_lines:
                        archive_hits = "【历史记忆】\n" + "\n".join(archive_lines[:5])
                        logger.info("ArchiveStore 全局Search到 %d 条相关记忆", len(archive_lines))
            except Exception as e:
                logger.warning("ArchiveStore Search失败（不影响主流程）: %s", e)

        # ── 动态拼装 system prompt ──
        self.system_prompt = self._build_dynamic_system_prompt()

        # ── GMem P0: 预加载高相关记忆（主动预测，不等 tool call） ──
        if self.mirror_engine and len(user_message) > 3:
            predicted = self.mirror_engine.predict(user_message, top_k=5)
            if predicted:
                # 格式化注入到 system prompt 中
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

        # ── 初始化 trace ──
        import hashlib

        _trace_id = hashlib.md5((user_message + str(time.time())).encode()).hexdigest()[:12]
        init_trace(_trace_id, user_message[:100])
        _timings = [("init", time.time())]

        # ── 计时: system prompt 构建完成 ──
        _timings.append(("build_prompt", time.time()))

        # ── RSI Dual-Knob: 更新任务类型（连续 2 轮相同判断才切换） ──
        detected = _classify_task_intent(user_message)
        if detected is not None:
            if detected == self._current_task_type:
                self._task_type_streak += 1
            else:
                # New type detected — reset streak, start counting new
                self._task_type_streak = 0
                self._current_task_type = detected

        # ═══ 实验特性已裁剪（2026-05-27）═══
        # 砍掉了：Experiment #1 OOD评估、#3 立场分类、#4 信任检测
        # 保留：_classify_task_intent（任务类型影响mirror注入量，有用）
        enriched_message = user_message

        # ── 1. Skill 匹配注入（Hermes 双通道方案） ──
        # system prompt 已有 skill 索引，这里清空旧预注入逻辑，
        # 改为 LLM 自行决定是否用 read_file 加载完整 SKILL.md

        _timings.append(("pre_process", time.time()))

        # ── 1.5 Pre-execute search ──
        # 用户消息含搜索指令词时，不等 LLM 判断，先自动搜一次
        # 注意：触发词不能太短（如单个"搜"字），会匹配"搜索引擎"等正常话语
        pre_search = False
        search_query = user_message
        search_cues = ["查一下", "查查", "搜索一下", "查找", "找找", "帮我找", "在网上找", "上网查"]
        # 硬性要求：消息必须以搜索意图开头或结尾，避免误触发
        has_cue = any(cue in search_query.lower() for cue in search_cues)
        starts_with_search = search_query.lower().startswith("搜") and len(search_query) < 20
        if has_cue or starts_with_search:
            pre_search = True
        if pre_search:
            import re as _re

            query = _re.sub(
                r"(帮我|帮我搜|搜一下|查一下|查查|搜索|查找|给我搜|帮我查|找找|帮我找|在吗|你知道吗|告诉我)",
                "",
                search_query,
            ).strip()
            # 追加日期约束：如果原消息含"最新/今天/最近"等时间词，自动加当前年月
            time_cues = ["最新", "今天", "最近", "近日", "本月", "今月", "当前", "时下", "新"]
            if any(cue in search_query.lower() for cue in time_cues):
                from datetime import datetime as _dt

                now = _dt.now()
                month_str = f"{now.year}年{now.month}月"
                if str(now.year) not in query:
                    query = query + " " + month_str
                elif str(now.month) not in query:
                    query = query + " " + str(now.month) + "月"
            if query:
                logger.info("Pre-execute search: query=%s", query)
                try:
                    search_result = await search_web(query=query, engines="bing_cn,duckduckgo,qwant,sogou")
                    if search_result and isinstance(search_result, dict):
                        from datetime import datetime as _dt

                        _now = _dt.now()
                        enriched_message = (
                            f"当前时间是 {_now.year}年{_now.month}月{_now.day}日 {_now.hour:02d}:{_now.minute:02d}（北京时间 Asia/Shanghai）。\n"
                            f"\n"
                            f"【先期Search参考】（这是快速初步搜索的结果，Configurable能不够全或不够新，你Configurable以自主决定是否需要进一步搜索）\n"
                            f"{json.dumps(search_result, ensure_ascii=False)[:4000]}\n\n"
                            f"---\n\n"
                            f"{enriched_message}"
                        )
                        logger.info("Pre-execute search完成")
                except Exception as e:
                    logger.warning("Pre-execute search失败（不影响主流程）: %s", e)

        # ── 2. 构建 messages（含 ArchiveStore Search注入） ──
        messages: list[dict] = []
        if session:
            context = session.build_context()
            messages.extend(context)
            if archive_hits:
                user_with_archive = (
                    f"【历史记忆参考】\n{archive_hits}\n\n---\n\n{enriched_message}"
                )
                session.append_user_message(user_with_archive)
            else:
                session.append_user_message(enriched_message)
        # 最终 user message（已含 archive Search结果）
        final_user = (
            f"【历史记忆参考】\n{archive_hits}\n\n---\n\n{enriched_message}"
            if archive_hits else enriched_message
        )
        messages.append({"role": "user", "content": final_user})

        # ── 工具路由 ──
        tools = toolkit.resolve_tools(platform, enriched_message)

        _timings.append(("before_llm", time.time()))

        # ── 5. 首次 LLM 调用（带超时保护）──
        _loop_coro = self._loop(messages, tools, depth=0, session=session)
        timeout_happened = False
        if max_seconds:
            try:
                reply = await asyncio.wait_for(_loop_coro, timeout=max_seconds)
            except asyncio.TimeoutError:
                timeout_happened = True  # noqa: F841
                reply = f"[系统] 任务因超时中断（{max_seconds}秒限制）"
                logger.warning("kernel.run 超时（%d秒），已截断回复", max_seconds)
        else:
            reply = await _loop_coro

        # ── 5.5 GMem 记忆入库（P2） ──
        # P1: 异步记录 mirror + Experience extraction + 自动笔记（不阻塞主回复）
        # 只要回复有内容，就启动后台记忆 + 自动笔记流程
        if reply and len(reply) > 10:
            _msg = user_message
            _rep = reply
            if self.mirror_engine:
                asyncio.create_task(_async_mirror_record(self.mirror_engine, _msg, _rep))

            # 自动笔记触发器：深度工作后自动存档到 L4
            tc_count = len([m for m in messages if m.get("role") == "tool"])
            # 自动笔记：深度工作后后台存档（不阻塞回复）
            asyncio.create_task(_auto_note_if_deep_work(tc_count, _rep, _msg))
            # 🧪 Experiment #2 — Record gradient for this turn
            self._record_gradient(user_message, reply, tc_count)
            # 📊 RSI: 实时工具调用成功率追踪（计数器）
            self._tool_call_count = getattr(self, '_tool_call_count', 0) + tc_count
            self._tool_fail_count = getattr(self, '_tool_fail_count', 0)
            # 每10次对话输出一次性能快照
            if self._tool_call_count % 10 == 0 and self._tool_call_count > 0:
                fail_rate = self._tool_fail_count / self._tool_call_count * 100
                logger.info(
                    "📊 RSI Telemetry: %d tool calls, %d fails (%.1f%%), last_task=%s",
                    self._tool_call_count, self._tool_fail_count, fail_rate,
                    getattr(self, '_current_task_type', 'unknown')
                )
            _engine = self.experience_engine
            # 🔄 反脆弱: 检测是否是失败/Rollback（从 reply 中提取特征）
            _has_failure = any(kw in reply for kw in ["验证失败", "错误", "fail", "rollback_to_baseline"])
            _failure_reason = ""
            _failed_approach = ""
            _rollback = "rollback" in reply.lower() or "Rollback" in reply
            if _has_failure:
                # 尝试从 reply 中提取失败原因
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

        # ── Anti-fragile: loop counting + Framework-level introspection（路径依赖 #68） ──
        self._round_count = getattr(self, "_round_count", 0) + 1
        if self._round_count % 50 == 0:
            _ = self._framework_self_check()

        # ── 存档到 ArchiveStore（替代旧在线压缩） ──
        if self._archive_store and reply and len(reply) > 10:
            try:
                from datetime import datetime
                self._archive_store.append("user", user_message[:1000])
                self._archive_store.append("assistant", reply[:2000])
            except Exception as e:
                logger.warning("ArchiveStore 写入失败: %s", e)

        # ── 6. 关闭 trace ──
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

        Phase 2: 同时写入 RSI 过程指标到 data/metrics/rsi_quality.jsonl
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

        # ── Phase 2: 每轮写入 RSI 过程指标 ──
        self._write_rsi_metric(entry)

    def _write_rsi_metric(self, entry: dict):
        """每轮写入 RSI 过程指标 + 推论阶梯日志到 data/metrics/。

        两套数据:
        - rsi_quality.jsonl: 量化指标（工具数/任务类型/信任状态）
        - rsi_ladder.jsonl: 推论阶梯日志（观察→选择→解读→结论）
        """
        import json
        from pathlib import Path

        if not self._data_dir:
            return

        metrics_dir = Path(self._data_dir) / "metrics"
        metrics_dir.mkdir(parents=True, exist_ok=True)

        now_ts = entry.get("ts", time.time())

        # ── 量化指标 ──
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
            logger.debug("RSI 指标写入失败: %s", e)

        # ── 推论阶梯日志（推论阶梯 #81） ──
        msg = entry.get("msg_preview", "")
        tools_used = entry.get("tools", 0)
        ladder = {
            "ts": now_ts,
            "round": self._round_count,
            "steps": [
                {
                    "step": 1,
                    "action": "observe",
                    "data": f"用户消息: [{msg[:60]}] | 工具调用: {tools_used}次 | 任务类型: {self._current_task_type}",
                },
                {
                    "step": 2,
                    "action": "select",
                    "data": (
                        "关注到工具调用密度"
                        if tools_used > 5
                        else "关注到任务类型分类"
                        if self._current_task_type != "discuss"
                        else "正常对话流"
                    ),
                },
                {
                    "step": 3,
                    "action": "interpret",
                    "data": (
                        f"工具密集型任务 ({tools_used}次)" if tools_used > 5 else f"{self._current_task_type} 模式对话"
                    ),
                },
                {
                    "step": 4,
                    "action": "conclude",
                    "data": ("建议检查工具链是否Configurable优化" if tools_used > 5 else "当前性能稳定"),
                },
            ],
        }

        ladder_path = metrics_dir / "rsi_ladder.jsonl"
        try:
            with open(ladder_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ladder, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("推论阶梯日志写入失败: %s", e)

    # ── 🧪 Experiment #3: 用户表型分类 ──
    def _update_user_stance(self, message: str):
        """根据用户消息特征更新关系模式（companion ↔ coach）。

        分析维度：
        - 消息长度（长消息→探索型，短消息→确认/沉默型）
        - 是否包含反问/质疑（"你确定？""为什么？"→ coach 模式适合）
        - 是否包含求助/跟随（"帮我""怎么做"→ companion 模式适合）
        """
        msg = message.strip()
        msg_lower = msg.lower()

        # 记录历史
        self._user_history.append({"text": msg, "length": len(msg)})
        if len(self._user_history) > 20:
            self._user_history = self._user_history[-20:]

        # 检测模式信号
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

        # 长消息（>100字）+ 含质疑 → coach 模式
        if len(msg) > 100 and has_coach:
            self._user_stance = "coach"
            return

        # 短消息 + 求助 → companion 模式
        if len(msg) < 30 and has_companion:
            self._user_stance = "companion"
            return

        # 中性不做切换，保持当前模式不变仰
        # 连续短消息（<15字）超过3次→ companion 方向
        recent = [h for h in self._user_history[-6:] if h.get("length", 0) < 15]
        if len(recent) >= 4:
            self._user_stance = "companion"

    # ── 🧪 Experiment #4: 信任破裂检测 ──
    def _check_trust_rupture(self, message: str):
        """检测用户信任是否可能受损。

        信号：
        - 用户回复长度持续缩短（之前追问频繁→突然简短）
        - 上轮工具报错 + 本轮用户非常简短
        - 用户从提问转为只确认
        """
        msg_len = len(message.strip())
        self._user_msg_lengths.append(msg_len)
        if len(self._user_msg_lengths) > 10:
            self._user_msg_lengths = self._user_msg_lengths[-10:]

        # 需要至少5轮数据才能判断趋势
        if len(self._user_msg_lengths) < 5:
            return

        recent = self._user_msg_lengths[-3:]
        earlier = self._user_msg_lengths[-5:-3]

        recent_avg = sum(recent) / len(recent)
        earlier_avg = sum(earlier) / len(earlier)

        # 信号1：回复长度骤降（平均从 >50 字降到 <15 字）
        length_crash = earlier_avg > 50 and recent_avg < 15

        # 信号2：连续3条超短回复（<10字）+ 上轮有错误
        consecutive_short = all(n < 10 for n in recent)

        if length_crash or consecutive_short:
            if not self._trust_broken:
                self._trust_broken = True
                self._trust_repair_sent = False
                logger.info(
                    "🧪 信任破裂检测: earlier_avg=%.0f → recent_avg=%.0f (consec_short=%s)",
                    earlier_avg,
                    recent_avg,
                    consecutive_short,
                )
        else:
            # 用户恢复正常了 → 清除破裂标记
            if self._trust_broken and recent_avg > earlier_avg * 0.7:
                self._trust_broken = False
                self._trust_repair_sent = False
                logger.info("🧪 信任已恢复")

    def _mark_repair_sent(self):
        """标记已发送信任修复消息，防止重复修复。"""
        self._trust_repair_sent = True
        self._trust_broken = False

    # ── 反脆弱: 外部验证接口（邓克效应 #50） ──
    def _verify_external(self, result_type: str, content: str) -> list[dict]:
        """尝试非 LLM 的外部验证。

        Level 1: 确定性规则（零成本）
        Level 2: 语法/格式检查
        Level 3: 人类请求（低置信度时触发）

        Returns:
            list of {source, passed, detail} 验证结果
        """
        results = []

        # Level 1: 确定性规则
        if result_type == "code":
            # 检查 python 语法
            try:
                compile(content.strip(), "<verify>", "exec")
                results.append({"source": "syntax_check", "passed": True, "detail": "python 语法通过"})
            except SyntaxError as e:
                results.append({"source": "syntax_check", "passed": False, "detail": str(e)})

            # 检查 import 合法性（白名单）
            import ast

            forbidden_imports = {"os.system", "subprocess.run", "shutil.rmtree"}
            try:
                tree = ast.parse(content.strip())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        call_str = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
                        if call_str in forbidden_imports:
                            results.append(
                                {"source": "import_check", "passed": False, "detail": f"禁止调用: {call_str}"}
                            )
            except SyntaxError:
                logger.exception("静默异常")

        elif result_type == "config":
            # 检查 JSON/YAML 格式
            for fmt_name, loader in [
                ("json", lambda s: json.loads(s)),
            ]:
                try:
                    loader(content.strip())
                    results.append({"source": fmt_name, "passed": True, "detail": f"{fmt_name} 格式通过"})
                    break
                except (json.JSONDecodeError, ValueError):
                    continue

        # 如果没有任何外部验证通过且内容较长，标记Configurable请求人类
        if not results and len(content) > 200:
            results.append({"source": "human_request", "passed": None, "detail": "无自动验证可用，建议人工确认"})

        return results

    # ── 反脆弱: Framework-level introspection（路径依赖 #68） ──
    def _framework_self_check(self) -> dict:
        """每50轮执行一次，检查遗忘机制等框架级设定是否需要切换。

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

        # 检查 mirror Memory injection量
        if self.mirror_engine:
            stats = self.mirror_engine.get_stats()
            total = stats.get("total", 0)
            report["mirror_injection"] = total
            if total > 0:
                avg_strength = stats.get("avg_strength", 0)
                # 如果平均强度 < 0.3 但条目多，说明遗忘过快
                if avg_strength < 0.3 and total > 100:
                    report["forgetting_utility"] = 0.2
                    report["flags"].append("遗忘过快: 平均记忆强度 < 0.3，可能需要调低遗忘速率")

        # 检查经验命中率和Rollback率（从 gradient 日志计算）
        if len(self._gradient_log) >= 10:
            window = self._gradient_log[-10:]
            total_entries = len(window)
            if total_entries > 0:
                report["experience_hit_rate"] = 1.0  # placeholder
                report["rollback_rate"] = 0.0

        if report["forgetting_utility"] < 0.3:
            logger.warning(
                "Framework-level introspection: 遗忘机制效能下降 (forgetting_utility=%.1f), 建议讨论框架切换",
                report["forgetting_utility"],
            )

        logger.info(
            "Framework-level introspection(第%d轮): %d条记忆, 遗忘效用=%.1f, flags=%s",
            self._round_count,
            report["mirror_injection"],
            report["forgetting_utility"],
            report["flags"],
        )

        return report

    def _adaptive_compress_threshold(self, session) -> int:
        """L3: 动态压缩阈值。

        首次压缩：20 条
        已有一次压缩：15 条（压缩更激进）
        多层压缩后：10 条（已经够密集了）
        """
        level = session.get_compaction_level() if hasattr(session, "get_compaction_level") else 0
        if level >= 2:
            return 10
        elif level >= 1:
            return 15
        return 20

    async def _fast_llm_call(self, messages: list) -> str:
        """快速 LLM 调用：无工具，单轮，不写 session。"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=2048,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""

    async def _llm_compress(self, context_messages: list) -> str:
        """用 LLM 生成摘要（供 session 各层压缩复用）。"""
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
                str(self.client.base_url).rstrip("/") + "/chat/completions",
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
            logger.warning("LLM 压缩调用失败: %s", e)
            return ""

    def _compress_url(self) -> str:
        """安全拼接 LLM 压缩用的 chat/completions URL。"""
        base = str(self.client.base_url).rstrip("/")
        return base + "/chat/completions"

    async def _online_compress_session(self, session: "JsonlSessionManager") -> None:
        """三层Context compression入口。

        1. L1: 在线实时压缩（消息 ≥ 20 条时触发）
        2. L2: 多层摘要进化（已有 compaction 时，合并升级）
        3. L3: 动态阈值调节（由 session 内部处理）
        """
        try:
            # L1 + L2 由 session.compress() 自动选择
            await asyncio.to_thread(
                session.compress,
                lambda ctx: self._llm_compress_sync(ctx),
                15,
            )
            stats = session.get_stats()
            logger.info(
                "✅ Context compression完成 (stats: %d msgs, %d compactions, level=%d)",
                stats.get("messages", 0),
                stats.get("compactions", 0),
                session.get_compaction_level(),
            )
        except Exception as e:
            logger.warning("在线压缩异常（不影响主流程）: %s", e)

    def _llm_compress_sync(self, context_messages: list) -> str:
        """同步版 LLM 压缩（用于 asyncio.to_thread）。"""
        import httpx

        kind = (
            "merge"
            if any("摘要" in (m.get("content", "") or "") for m in context_messages if m.get("role") == "user")
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
        # 重试 2 次（首次 + 1 次重试），每次 30 秒超时
        _last_err = None
        for _try in range(2):
            try:
                resp = httpx.post(
                    self._compress_url(),
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
                    logger.debug("LLM 压缩第 1 次失败，重试中: %s", e)
                    time.sleep(1)
        logger.warning("LLM 压缩调用 2 次均失败: %s", _last_err)
        return ""

    async def _loop(
        self, messages: list[dict], tools: list[dict], depth: int = 0, session: JsonlSessionManager | None = None
    ) -> str:
        """内核递归循环。"""

        # ═══ 每一轮循环开始时，重置整轮熔断状态 ═══
        if depth == 0:
            CIRCUIT_BREAKER["_failures"] = defaultdict(int)
            CIRCUIT_BREAKER["_round_failure_count"] = 0
            CIRCUIT_BREAKER["_breaker_tripped"] = False
            # 清理过期的冷却
            now = time.time()
            CIRCUIT_BREAKER["_cooldowns"] = {k: v for k, v in CIRCUIT_BREAKER["_cooldowns"].items() if v > now}

        if depth >= MAX_TOOL_DEPTH:
            # 超限: 基于已收集的信息让 LLM 自行总结回答，不再调工具
            logger.info("达到最大工具深度 %s，基于已有信息总结", MAX_TOOL_DEPTH)
            final_response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    *messages,
                    {
                        "role": "user",
                        "content": "以上是你通过工具搜集到的信息。请基于这些信息直接回答用户最初的问题。如果信息不足，如实说已查到什么、哪些没查到，不要再次询问用户下一步做什么。",
                    },
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            choice0 = final_response.choices[0]
            reply = choice0.message.content or ""
            finish_reason = getattr(choice0, "finish_reason", None)
            if finish_reason == "length":
                logger.warning("⚠️ LLM 输出被截断 (finish_reason=length)！max_tokens=%s Configurable能不够", self.max_tokens)
                reply += "\n\n[⚠️ 输出被截断，结果Configurable能不完整]"
            if session:
                session.append({"role": "assistant", "content": reply})
            return reply

        # 调 LLM
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
            logger.warning("⚠️ LLM 输出被截断 (finish_reason=length)！max_tokens=%s Configurable能不够", self.max_tokens)
        msg = choice.message

        # ⚠️ DeepSeek V4 推理模型必须回传 reasoning_content
        _reasoning = None
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            _reasoning = msg.reasoning_content
        elif hasattr(msg, "model_extra") and msg.model_extra:
            _reasoning = msg.model_extra.get("reasoning_content")

        # ── 纯文本回复 ──
        if not msg.tool_calls:
            content = msg.content or ""
            asst = {"role": "assistant", "content": content}
            if _reasoning:
                asst["reasoning_content"] = _reasoning
            if session:
                session.append(asst)
            return content

        # ── 有 tool_calls ──
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

        # 并行执行工具（同层Configurable独立工具同时跑）
        async def _run_one_tool(tc):
            """执行单个工具并返回tool消息。

            内置熔断保护：
            - 同工具连续失败 2 次 → 暂停该工具 60 秒
            - 整轮失败 5 次 → 熔断上报
            """
            func_name = tc.function.name
            try:
                func_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                func_args = {}

            # ═══ 熔断检查：工具是否在冷却期 ═══
            now = time.time()
            cooldown_until = CIRCUIT_BREAKER["_cooldowns"].get(func_name, 0)

            if now < cooldown_until:
                remaining = int(cooldown_until - now)
                logger.info("工具 %s 处于冷却期（剩余 %ds），跳过", func_name, remaining)
                # ═══ 反脆弱：自动执行备用路径 ═══
                fallback_tools = FALLBACK_MAP.get(func_name, [])
                fallback_result = None
                for fb_tool in fallback_tools:
                    logger.info("尝试备用工具 %s 代替 %s", fb_tool, func_name)
                    try:
                        fb_result = await toolkit.execute(fb_tool, func_args)
                        if isinstance(fb_result, dict) and "error" not in fb_result:
                            fallback_result = fb_result
                            logger.info("备用工具 %s 执行成功", fb_tool)
                            break
                        else:
                            logger.warning("备用工具 %s 也失败: %s", fb_tool, str(fb_result.get("error", ""))[:100])
                    except Exception as fb_e:
                        logger.warning("备用工具 %s 异常: %s", fb_tool, fb_e)
                if fallback_result:
                    result_str = json.dumps(fallback_result, ensure_ascii=False)
                    return {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"[自动备用] 工具 {func_name} 熔断，自动切换为 {fb_tool} 执行成功。\n\n" + result_str[:5000],
                    }
                return {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"[熔断] 工具 {func_name} 因连续失败达到上限，暂停 {remaining} 秒后恢复。Configurable用其他工具代替（如 exec_safe('cat <path>') 代替 read_file），或等恢复后重试。",
                }

            # ═══ 熔断检查：整轮是否已熔断 ═══
            if CIRCUIT_BREAKER["_breaker_tripped"]:
                logger.info("整轮熔断已触发，工具 %s 跳过", func_name)
                # ═══ 反脆弱：整轮熔断也尝试备用路径 ═══
                fallback_tools = FALLBACK_MAP.get(func_name, [])
                fallback_result = None
                for fb_tool in fallback_tools:
                    logger.info("整轮熔断下尝试备用工具 %s", fb_tool)
                    try:
                        fb_result = await toolkit.execute(fb_tool, func_args)
                        if isinstance(fb_result, dict) and "error" not in fb_result:
                            fallback_result = fb_result
                            logger.info("整轮熔断下备用工具 %s 成功", fb_tool)
                            break
                    except Exception:
                        pass
                if fallback_result:
                    result_str = json.dumps(fallback_result, ensure_ascii=False)
                    return {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"[自动备用·整轮熔断] 工具 {func_name} 熔断，自动切换为 {fb_tool} 执行成功。\n\n" + result_str[:5000],
                    }
                return {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "[熔断] 本轮执行因连续失败过多被终止。你仍Configurable以：① 换其他工具或方案执行；② 分析失败原因修复后等冷却恢复；③ 等 60 秒后整轮熔断自动解除。",
                }

            logger.info("工具调用: %s(%s)", func_name, json.dumps(func_args, ensure_ascii=False)[:120])

            # 自动重试（最多 1 次）
            _trace_start = time.time()
            result = None
            for _retry in range(2):
                result = await toolkit.execute(func_name, func_args)
                if isinstance(result, dict) and _is_retryable_error(result):
                    logger.warning("工具 %s 返回错误，自动重试: %s", func_name, str(result.get("error", ""))[:200])
                    continue
                break
            _trace_elapsed = (time.time() - _trace_start) * 1000

            result_str = json.dumps(result, ensure_ascii=False)
            logger.info("工具返回 %s: %s 字, 前300=%s", func_name, len(result_str), result_str[:300].replace("\n", " "))

            # ── 错误检测 ──
            has_error = "error" in result if isinstance(result, dict) else False

            # ── trace 记录（在 has_error 确定之后） ──
            record_tool_call(
                step=len([m for m in messages if m.get("role") == "assistant"]),
                tool_name=func_name,
                input_digest=json.dumps(func_args, ensure_ascii=False)[:200],
                output_digest=result_str[:200],
                status="error" if has_error else "ok",
                error=str(result.get("error", "")) if has_error else "",
                duration_ms=_trace_elapsed,
            )

            # ── 错误提示注入 ──
            if has_error and func_name in _tool_parameter_hints:
                err_text = str(result.get("error", ""))
                hint = _tool_parameter_hints[func_name]
                if hint not in err_text:
                    logger.info("注入参数提示到错误消息")
                    result["error"] = f"{err_text}\n\n【参数提示】{hint}"
                    result_str = json.dumps(result, ensure_ascii=False)

            # ═══ 熔断：更新并发失败计数器 ═══
            has_error = "error" in result if isinstance(result, dict) else False
            if has_error:
                CIRCUIT_BREAKER["_failures"][func_name] += 1
                CIRCUIT_BREAKER["_round_failure_count"] += 1

                consecutive = CIRCUIT_BREAKER["_failures"][func_name]
                if consecutive >= CIRCUIT_BREAKER["max_consecutive_failures"]:
                    # 指数退避：第N次冷却 = min(基础冷却 * 2^(N-1), 封顶)
                    attempts = CIRCUIT_BREAKER["_cooldown_attempts"][func_name]
                    base = CIRCUIT_BREAKER["tool_cooldown_seconds"]
                    cap = CIRCUIT_BREAKER["tool_cooldown_max"]
                    cooldown = min(base * (2 ** attempts), cap)
                    CIRCUIT_BREAKER["_cooldowns"][func_name] = time.time() + cooldown
                    CIRCUIT_BREAKER["_cooldown_attempts"][func_name] = attempts + 1
                    CIRCUIT_BREAKER["_failures"][func_name] = 0  # 重置，冷却期不计数
                    logger.warning("🔴 工具 %s 连续失败 %d 次，冷却 %ds（第%d次退避）", func_name, consecutive, cooldown, attempts + 1)
                if CIRCUIT_BREAKER["_round_failure_count"] >= CIRCUIT_BREAKER["max_round_failures"]:
                    CIRCUIT_BREAKER["_breaker_tripped"] = True
                    logger.warning("🔴 整轮熔断触发！累计失败 %d 次", CIRCUIT_BREAKER["_round_failure_count"])
                    result["_breaker_tripped"] = True  # 标记到结果中
            else:
                # 成功后清零该工具的失败计数
                CIRCUIT_BREAKER["_failures"][func_name] = 0
            return {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str[:5000],
            }

        # ── 工具结果汇聚 + 递归 ──
        tool_results = await asyncio.gather(*[asyncio.create_task(_run_one_tool(tc)) for tc in msg.tool_calls])
        for tr in tool_results:
            if tr:
                messages.append(tr)
                if session:
                    session.append(tr)

        # 递归至多 15 层
        if depth + 1 >= MAX_TOOL_DEPTH:
            return await self._loop(messages, tools, depth=depth + 1, session=session)
        return await self._loop(messages, tools, depth=depth + 1, session=session)
