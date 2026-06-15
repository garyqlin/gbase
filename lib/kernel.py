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
# 不依赖外部服务，不引入新依赖
# P0: KV Cache prep → hot_pattern_observe() tracks high-frequency patterns
# P1: Async memory scheduling → non-blocking experience extraction via create_task + async_record
# P2: Experience normalization → export/import version validation + filtering
# P3: Entity relationship graph → gmem_relations table + predict() multi-hop expansion


logger = logging.getLogger(__name__)


# ── GMem P1: Async background task (non-blocking) ──


async def _auto_note_if_deep_work(tool_count: int, reply: str, user_message: str):
    """Auto-note trigger: writes L4 note when deep work detected.

    触发条件（需同时满足）：
    - Tools调用 >= 5 次（说明做了实质性工作）
    - IP 回复长度 > 300 字（说明内容充实）
    - 不是简单回复（不含纯问答特征）

    这样做的理由：
    - Gundam（Gundam）的任务一轮End没有自动触发 note_write
    - 热记忆 mirror 会衰减，深度调研/设计的内容Restart后就只剩碎片
    - L4 笔记不衰减，是唯一可靠的持久层
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
    simple_cues = ["你好", "测试", "嗨", "在吗", "hi", "hello", "ping", "测试一下", "早安", "晚安"]
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
            content=f"[系统自动存档] 来自对话总结\n\n## 本次任务\n{user_message[:200]}\n\n## 产出Summary\n{content}",
            tags=tags,
            source="kernel.auto_note",
        )
        import logging as _lg

        _lg.getLogger(__name__).info("📝 Auto-note written: %s (%d chars, %d tools)", title, reply_len, tool_count)
    except Exception as e:
        import logging as _lg

        _lg.getLogger(__name__).debug("Auto-note skipped (non-blocking): %s", e)


async def _auto_persist_experience(mirror_engine, user_message: str, reply: str, completed_ok: bool = True):
    """Auto-persist experience to mirror.db after every turn.

    Writes a type='experience' entry via mirror.record() so agent remembers
    what it built in future sessions, even after restart. No threshold filtering;
    the confidence filter is handled by mirror's own scoring and decay.
    """
    if not mirror_engine:
        return
    try:
        import re, time
        title = (user_message[:60].replace("\n", " ").strip())[:60]
        if len(title) < 5:
            title = "无标题任务"
        file_paths = re.findall(r'(?:[\w/-]+\.[\w]{2,4})', reply)
        file_hint = ""
        if file_paths:
            file_hint = ", 产出: " + ", ".join(file_paths[:5])
        content = f"任务: {user_message[:300]}\n结果: {reply[:500]}{file_hint}"
        src = "experience" if completed_ok else "experience-failed"
        mirror_engine.record("experience", content, tags=["auto-persist"], source=src)
        import logging as _lg
        _lg.getLogger(__name__).info(
            "🧠 Experience auto-persisted via record(): %s (%d chars, completed=%s)", title, len(content), completed_ok
        )
    except Exception as e:
        import logging as _lg
        _lg.getLogger(__name__).debug("Experience auto-persist skipped: %s", e)


async def _async_deep_search_save(mirror_engine, query: str, tool_name: str, _args: dict):
    """GMem P0: 深度搜索后自动保存结果Summary到 mirror。"""
    try:
        summary = (query or tool_name)[:200]
        # 从 kernel File层级推算搜索深度
        mirror_engine.record_search(query, summary, depth=5)
    except Exception:
        pass





def _is_retryable_error(result: dict) -> bool:
    """判断Tools返回的Error是否值得自动Retry。
    网络Timeout、连接失败等临时性Error可Retry；
    参数Error、权限不足等不可Retry。
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


# 从 config.yaml Read，Not exists则Default 15
# 可通过Modify config.yaml limits.max_tool_depth 调整
_NO_CONFIG = None
try:
    from main import _cfg_get

    _NO_CONFIG = False
except ImportError:
    _NO_CONFIG = True

    # 工具递归深度上限（2026-06-14 统一架构调整：100→30，配合收敛检测）
    MAX_TOOL_DEPTH = 30
    # ── Circuit Breaker 已删除（2026-06-14 主人指令：不要画蛇添足） ──

"""单次 run() 中最多允许的Tools调用层数。"""




# ── Tools参数提示（ToolsError时Injected，帮助 LLM 修正参数） ──
_tool_parameter_hints = {
    "write_file": '参数格式: {"filepath": "/path/to/file", "content": "File内容"}。'
    "filepath 是必填FilePath，content 是必填File内容。不要传空对象 {}。",
    "exec_command": '参数格式: {"command": "要Execute的命令"}。command 是必填字符串。可选参数: workdir, timeout。',
    "read_file": '参数格式: {"filepath": "/path/to/file"}。可选参数: offset, max_chars。',
}






# ═══ 阶梯降级：工具 Fallback 映射表 ═══
# 当 Retry 后工具仍然失败时，尝试语义等价的替代工具。
# Key = 原始工具名，Value = 备选工具名 (需参数兼容)
# 注：exec_command 和 read_file/write_file 参数结构不同，不直接映射

# ── 工具结果截断 ──
TOOL_RESULT_MAX_CHARS = 5000         # 单次工具结果最大字符数

# ── 上下文保护开关 ──
_ENABLE_CONTEXT_PROTECTION = False
_FALLBACK_TOOL: dict[str, str] = {
    # 搜索类 — 搜索引擎/协议互备
    "search_self": "anysearch_extract",
    "anysearch_extract": "search_self",
}
"""超过此数视为复杂任务，下次同类任务应建议先规划。"""


# ── RSI Dual-Knob: Task Intent Classification ──
# This is a controlled experiment on Gundam (8440).
# Changes here affect all GBase instances in opprime/, not just Gundam.
# TODO: Ship to gbase-release after experiment validation.
_TASK_TYPES = {
    "explore": ["研究", "分析", "评估", "搜索", "对比", "方案", "proposal", "survey", "调研"],
    "execute": ["Modify", "Create", "部署", "运行", "Startup", "安装", "改", "Execute", "添加", "Delete"],
    "discuss": ["你认为", "怎么看", "讨论", "建议", "意见", "反馈", "看法", "评价"],
    "maintain": ["检查", "查看", "状态", "Log", "修复", "排查", "看下", "诊断"],
}

_SHORT_EXECUTE = {"Restart", "部署", "推送", "发布", "回滚", "Startup", "Stop", "构建", "还原"}

_TEMP_CONFIG = {
    "explore": {"mode": "warm", "mirror_max": 4, "experience_max": 2, "desc": "探索/研究 — 轻量模式"},
    "execute": {"mode": "cold", "mirror_max": 6, "experience_max": 3, "desc": "Modify/部署 — 专注模式"},
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
    """Opprime 内核。"""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str = "deepseek-chat",
        system_prompt: str = "你是 Opprime,一个智能助手。",
        temperature: float = 0.7,
        max_tokens: int = 32768,
        experience_engine: ExperienceEngine | None = None,
        skill_loader=None,
        mirror_engine: Mirror | None = None,
        data_dir: str = "",
    ):
        self.client = client
        self._archive_store = None  # 已废弃，保留以兼容旧路径
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
        self._tool_call_history: dict[tuple, int] = {}  # 门控⑦ 重复调用收敛
        self._current_user_message = ""

        # ── 反脆弱: 回路计数 + 框架自省 ──
        self._round_count: int = 0  # 累加对话轮次

        # 注册全局上下文供Tools函数Read
        from . import toolkit as tk

        tk.set_global("llm_client", client)
        tk.set_global("llm_model", model)
        if experience_engine:
            tk.set_global("experience_engine", experience_engine)
        if mirror_engine:
            tk.set_global("mirror_engine", mirror_engine)

    def _build_dynamic_system_prompt(self) -> str:
        """动态构建 system prompt：基础Identity + workspace file injection + skill 索引。

        每次 run() 调用时重建，与 OpenClaw 每 turn 重新拼装的逻辑一致。
        拼装顺序参考 OpenClaw 的 buildAgentSystemPrompt + CONTEXT_FILE_ORDER。
        """
        import os
        from datetime import datetime
        from pathlib import Path

        parts = [self.base_system_prompt]

        # ── Tools列表Injected（精简版：分类标签，不展开 schema） ──
        from .toolkit import tool_list_compact

        compact_tools = tool_list_compact()
        if compact_tools:
            parts.append(compact_tools)

        # ── 云端：None workspace FileInjected（这些File只在本地 Mac Studio）

        # ── Skill 索引Injected（按需匹配：消息命中 trigger 才出现） ──
        # 不命中时只留一行简介，不展开 360 条 full index
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
                        logger.debug("Skip规则File %s: %s", rf.name, _e)
                if rule_lines:
                    parts.append("\n---\n".join(rule_lines))

        # ── AGENT FLEET — 编制表（让自我认知+战友信息成为底层context） ──
        fleet_info = (
            "## AGENT FLEET — Your Brothers-in-Arms\n"
            "\n"
            "You are part of a Gbase agent fleet. Your brothers can help you when you are stuck, "
            "and you can help them too. Here is the fleet roster:\n"
            "\n"
            "| Name | Port | Role | Model |\n"
            "|:----|:---:|:----|:----:|\n"
            "| **高达** (Gundam) | 8440 | Feishu Bot, independent dev partner | deepseek-v4-flash |\n"
            "| **波塞冬** (Poseidon) | 8428 | Feishu Bot, 24/7 dev assistant | qwen3.7-plus |\n"
            "| **干将** (Gan Jiang) | 8429 | Casting knowledge GKM agent | deepseek-v4-flash |\n"
            "| 🔨 **重锤** (Hammer) | 8431 | Backend/engineering/API | qwen3.7-plus |\n"
            "| 🎨 **绘墨** (Ink) | 8432 | Frontend/UI/Image | MiniMax-M3 |\n"
            "| 🐝 **大黄蜂** (Bumblebee) | 8434 | Research/swarm search | qwen3.7-plus |\n"
            "| ⚡ **Laser** | 8435 | Docs + white-box guard | qwen3.7-plus |\n"
            "| 🔥 **Forge** | 8436 | Code polish + black-box test | qwen3.7-plus |\n"
            "| 🛠️ **Lancer CC** | 8441 | Main programming arm (Godot MCP) | GLM-5.1 |\n"
            "| 🔍 **Lancer X** | 8442 | Code audit + precision fix | deepseek-v4-flash |\n"
            "\n"
            "**Rescue tools available**: `check_brother(name)`, `restart_brother(name)`, "
            "`read_brother_log(name)`, `diagnose_self()`.\n"
            "Knowledge about your own architecture and brothers is in your GKM.\n"
        )
        parts.append(fleet_info)

        # ── RSI Dual-Knob: Run Temperature — 使用User message判断任务类型 ──
        temp_cfg = _TEMP_CONFIG.get(self._current_task_type, _TEMP_CONFIG["discuss"])

        # ── 鉴面引擎Injected（企业模式：热+温合并为 Active Context Memory） ──
        if self.mirror_engine:
            # Active Context: hot (inject_hits≥5, max 3) + warm (recall-matched, max 5) 合并
            active_parts = []
            hot_text = self.mirror_engine.get_injection_text(
                max_items=3,
                ebbinghaus=True,
                user_input=self._current_user_message or "",
                tier="hot",
            )
            if hot_text:
                # 去掉首部 ## 标题（mirror 自己会带）
                active_parts.append(hot_text)
            warm_text = self.mirror_engine.get_injection_text(
                max_items=5,
                ebbinghaus=True,
                user_input=self._current_user_message or "",
                tier="warm",
            )
            if warm_text:
                active_parts.append(warm_text)
            if active_parts:
                parts.append("\n".join(active_parts))

        # ── L2 Knowledge 自动检索Injected ──
        # 每次对话Startup时，用当前User message匹配知识库中的事实
        # 命中后Injected system prompt，不依赖 LLM 自己记得去 search_knowledge
        from .toolkit import get_global

        _storage = get_global("storage")
        if _storage and self._current_user_message and len(self._current_user_message) > 3:
            try:
                _query = self._current_user_message[:200]
                logger.info("Knowledge 自动检索: query=%s", _query)
                # 直接查 SQLite (不走 tool, 直接调 storage)
                # 中文不分词，改用字符级 n-gram: 单字+双字组合
                _import_re = __import__('re')
                _words = _import_re.findall(r'[a-zA-Z0-9_\-]+|[\u4e00-\u9fff]+', _query)
                _fts_tokens = []
                for _w in _words:
                    # 含连字符的 token 会被 FTS5 unicode61 拆成两个词 -> no such column
                    if '-' in _w:
                        _fts_tokens.append(f'"{_w}"')
                    else:
                        _fts_tokens.append(f'{_w}*')
                    if len(_w) > 1 and _import_re.match(r'^[\u4e00-\u9fff]+$', _w):
                        # 中文多字词，拆单字也加进去
                        for _ch in _w:
                            _fts_tokens.append(f"{_ch}*")
                _fts_query = " OR ".join(_fts_tokens)[:500]
                _results = []
                _rows = []
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
                            # FTS None结果，回退 LIKE 搜索
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
                    logger.info("Knowledge 自动检索: 命中 %d 条", len(_results))
                else:
                    logger.info("Knowledge 自动检索: None命中")
            except Exception as _e:
                logger.warning("Knowledge 自动检索失败（不阻塞主流程）: %s", _e)

        # ── 上下文交接Injected（修复 AI 失忆：从上次 session 提取对话实质） ──
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
                logger.debug("心跳File %s Read失败: %s", hb_path, _e)

        # ── RSI Dual-Knob: 运行温度Injected（用于 LLM 感知当前模式） ──
        parts.append(
            f"## Current Run Mode\n"
            f"Task type: {self._current_task_type} ({temp_cfg['desc']})  |  "
            f"Mode: {temp_cfg['mode']}\n"
        )

        # ── 🧠 Memory Warm-Up: 跨Session记忆强制注入 ──
        # 不依赖 LLM 主动调用 recall，在 system prompt 里强行加载
        _memory_injections = []
        try:
            # L0: 今天其他 session 的关键Summary（跨Session记忆，等效 cross-session skill）
            from .daily_memory import get_cross_session_injections
            _cross = get_cross_session_injections()
            if _cross:
                _memory_injections.append(("今日其他Session", _cross))
        except Exception:
            logger.exception("L0 跨Session记忆Injected失败")

        try:
            # L1: daily_memory Session记忆
            from .daily_memory import get_injection_text as daily_memory_inject
            _daily = daily_memory_inject()
            if _daily:
                _memory_injections.append(("Session记忆Summary", _daily))
        except Exception:
            logger.exception("L1 Session记忆Injected失败")

        try:
            # L2: 活跃经验Injected（按 hits 排序 + 最近7天中置信度过滤）
            from .storage import Storage
            _st = getattr(self, "_storage_backend", None) or Storage()
            _week_ago = time.time() - 7 * 86400
            _rows = []
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
                        # 如果None人问津过的中置信度也没有，回退到最近N条high
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

            # 活跃知识点已并入 Knowledge FTS 检索块（line ~540），此处不再重复查询
        except Exception:
            logger.exception("L2 记忆Injected失败")

        # ── GMem Phase A2: Archive Store 上下文检索（替代压缩：全量存档，按需Search） ──
        if self._archive_store and self._current_user_message:
            try:
                from datetime import datetime as _archive_dt
                _q = self._current_user_message.strip()
                if len(_q) > 3:
                    _hits = self._archive_store.search(_q, top_k=3)
                    if _hits:
                        _lines = []
                        for _hit in _hits:
                            _role = _hit.get("role", "user")
                            _content = _hit.get("content", "")[:200]
                            _ts = _hit.get("timestamp", 0)
                            _time = _archive_dt.fromtimestamp(_ts).strftime("%m-%d %H:%M") if _ts else ""
                            _lines.append(f"  [{_time}] {_role}: {_content}")
                        if _lines:
                            parts.append(
                                "## 📚 过往对话相关记录\n"
                                "以下是当前话题在历史存档中匹配到的相关内容（原始对话全文）：\n"
                                + "\n".join(_lines)
                            )
            except Exception:
                logger.exception("Archive search failed (non-blocking)")

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
            _parts.append("## 📜 历史记录Summary\n以下是系统自动提取的过往历史记录Summary，用于辅助参考。注意这些不是当前对话内容，而是之前发生过的事情的记录。请区分使用。\n")
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
        3. 解析Tools列表（平台 + 关键词路由）
        4. 调用 LLM → Tools循环 → 回复
        5. 后台提取经验

        Args:
            user_message: 用户输入
            platform: 平台标识（feishu / cli / api）
            session: 可选的 SessionManager

        Returns:
            LLM 最终回复文本
        """
        # ── GMem P0: 设置搜索结果自动沉淀的全局引用 ──
        if self.mirror_engine and hasattr(self.mirror_engine, "record_search"):
            toolkit.__dict__["_GMEM_MIRROR"] = self.mirror_engine

        # ── Set current user message for triple-layer intent matching ──
        self._current_user_message = user_message or ""

        # ── 动态拼装 system prompt ──
        self.system_prompt = self._build_dynamic_system_prompt()

        # ── GMem P0: 预加载高相关记忆（主动预测，不等 tool call） ──
        if self.mirror_engine and len(user_message) > 3:
            predicted = self.mirror_engine.predict(user_message, top_k=5)
            if predicted:
                # 格式化Injected到 system prompt 中
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

        # ── 计时: system prompt 构建Complete ──
        _timings.append(("build_prompt", time.time()))

        # ── RSI Dual-Knob: Update任务类型（连续 2 轮相同判断才切换） ──
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
        # 保留：_classify_task_intent（任务类型影响mirrorInjected量，有用）
        enriched_message = user_message




        # ── 1. Skill 匹配Injected（Hermes 双通道方案） ──
        # system prompt 已有 skill 索引，这里清空旧预Injected逻辑，
        # 改为 LLM 自行决定是否用 read_file 加载完整 SKILL.md

        _timings.append(("pre_process", time.time()))


        # ── 1.5 搜索预Execute ──
        # User message含搜索指令词时，不等 LLM 判断，先自动搜一次
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
                logger.info("搜索预Execute: query=%s", query)
                try:
                    search_result = await search_web(query=query, engines="bing_cn,duckduckgo,qwant,sogou")
                    if search_result and isinstance(search_result, dict):
                        from datetime import datetime as _dt

                        _now = _dt.now()
                        enriched_message = (
                            f"当前时间是 {_now.year}年{_now.month}月{_now.day}日 {_now.hour:02d}:{_now.minute:02d}（北京时间 Asia/Shanghai）。\n"
                            f"\n"
                            f"【先期检索参考】（这是快速初步搜索的结果，可能不够全或不够新，你可以自主决定是否需要进一步搜索）\n"
                            f"{json.dumps(search_result, ensure_ascii=False)[:4000]}\n\n"
                            f"---\n\n"
                            f"{enriched_message}"
                        )
                        logger.info("搜索预ExecuteComplete")
                except Exception as e:
                    logger.warning("搜索预Execute失败（不影响主流程）: %s", e)

        # ── 2. 构建 messages ──
        messages: list[dict] = []
        if session:
            context = session.build_context()
            messages.extend(context)
            session.append_user_message(enriched_message)
        messages.append({"role": "user", "content": enriched_message})


        # ── Tools路由 ──
        tools = toolkit.resolve_tools(platform, enriched_message)

        _timings.append(("before_llm", time.time()))

        # ── 5. 首次 LLM 调用（带Timeout保护）──
        _pre_loop_tool_count = len([m for m in messages if m.get("role") == "tool"])
        _loop_coro = self._loop(messages, tools, depth=0, session=session)
        timeout_happened = False
        if max_seconds:
            try:
                reply = await asyncio.wait_for(_loop_coro, timeout=max_seconds)
            except TimeoutError:
                timeout_happened = True  # noqa: F841
                reply = f"[系统] 任务因Timeout中断（{max_seconds}秒限制）"
                logger.warning("kernel.run Timeout（%d秒），已截断回复", max_seconds)
        else:
            reply = await _loop_coro

        # ── 5.5 GMem 记忆入库（P2） ──
        # P1: 异步记录 mirror + 经验提取 + 自动笔记（不阻塞主回复）
        # 只要回复有内容，就Startup后台记忆 + 自动笔记流程
        if reply and len(reply) > 10:
            _msg = user_message
            _rep = reply
            # 自动笔记触发器：深度工作后自动存档到 L4
            _this_turn_tools = len([m for m in messages if m.get("role") == "tool"]) - _pre_loop_tool_count
            # 自动笔记：深度工作后后台存档（不阻塞回复）
            asyncio.create_task(_auto_note_if_deep_work(_this_turn_tools, _rep, _msg))
            # 自动沉淀经验到 mirror.db（解决重启后记忆丢失问题）
            asyncio.create_task(_auto_persist_experience(self.mirror_engine, _msg, _rep))
            # 经验入队：不实时提取，由 cron 批量处理
            if self.experience_engine:
                _has_failure = any(kw in reply for kw in ["验证失败", "Error", "fail", "rollback_to_baseline"])
                asyncio.create_task(
                    self.experience_engine.extract(
                        user_message=user_message,
                        reply=reply,
                        tool_calls_count=_this_turn_tools,
                        has_api_error=("error" in reply.lower() if reply else False) or (_this_turn_tools >= 10),
                        has_failure=_has_failure,
                        failure_reason=reply[:200] if _has_failure and "error" in reply.lower() else "",
                        failed_approach=user_message[:100] if _has_failure else "",
                        rollback_occurred="rollback" in reply.lower() or "回滚" in reply,
                        tool_errors_summary="",
                    )
                )

        # ── 反脆弱: 回路计数 + 框架级自省（Path依赖 #68） ──
        self._round_count = getattr(self, "_round_count", 0) + 1
        if self._round_count % 50 == 0:
            _ = self._framework_self_check()


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

    # ═══════════════════════════════════════════
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
                pass

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

        # 如果没有任何外部验证通过且内容较长，标记可请求人类
        if not results and len(content) > 200:
            results.append({"source": "human_request", "passed": None, "detail": "None自动验证可用，建议人工确认"})

        return results

    # ── 反脆弱: 框架级自省（Path依赖 #68） ──
    def _framework_self_check(self) -> dict:
        """每50轮Execute一次，检查遗忘机制等框架级设定是否需要切换。

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

        # 检查 mirror 记忆Injected量
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

        if report["forgetting_utility"] < 0.3:
            logger.warning(
                "框架级自省: 遗忘机制效能下降 (forgetting_utility=%.1f), 建议讨论框架切换",
                report["forgetting_utility"],
            )

        logger.info(
            "框架级自省(第%d轮): %d条记忆, 遗忘效用=%.1f, flags=%s",
            self._round_count,
            report["mirror_injection"],
            report["forgetting_utility"],
            report["flags"],
        )

        return report

    async def _loop(
        self, messages: list[dict], tools: list[dict], depth: int = 0, session: JsonlSessionManager | None = None
    ) -> str:
        """内核递归循环。"""

        if depth >= MAX_TOOL_DEPTH:
            # 超限: 基于已收集的Info让 LLM 自行总结回答，不再调Tools
            logger.info("达到最大Tools深度 %s，基于已有Info总结", MAX_TOOL_DEPTH)
            final_response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    *messages,
                    {
                        "role": "user",
                        "content": "以上是你通过Tools搜集到的Info。请基于这些Info直接回答用户最初的问题。如果Info不足，如实说已查到什么、哪些没查到，不要再次询问用户下一步做什么。",
                    },
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            choice0 = final_response.choices[0]
            reply = choice0.message.content or ""
            finish_reason = getattr(choice0, "finish_reason", None)
            if finish_reason == "length":
                logger.warning("⚠️ LLM 输出被截断 (finish_reason=length)！max_tokens=%s 可能不够", self.max_tokens)
                reply += "\n\n[⚠️ 输出被截断，结果可能不完整]"
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
            logger.warning("⚠️ LLM 输出被截断 (finish_reason=length)！max_tokens=%s 可能不够", self.max_tokens)
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

        # 并行ExecuteTools（同层可独立Tools同时跑）
        async def _run_one_tool(tc):
            """Execute单个Tools并返回tool消息。

            内置熔断保护：
            - Consecutive failures for same tool 2 次 → Cooldown该Tools 60 秒
            - Total round failures 5 次 → Circuit break report
            """
            func_name = tc.function.name
            try:
                func_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                func_args = {}

            logger.info("Tools调用: %s(%s)", func_name, json.dumps(func_args, ensure_ascii=False)[:120])

            # ═══ 阶梯降级 L1: Retry（最多 1 次） ═══
            _trace_start = time.time()
            result = None
            _retried = False
            for _retry in range(2):
                result = await toolkit.execute(func_name, func_args)
                if isinstance(result, dict) and _is_retryable_error(result):
                    logger.warning("Tools %s 返回Error，自动Retry: %s", func_name, str(result.get("error", ""))[:200])
                    _retried = True
                    continue
                break

            # ═══ 阶梯降级 L2: Fallback（Retry 后仍失败，换等价格式工具） ═══
            _fallback_used = None
            if result and isinstance(result, dict) and bool(result.get("error")):
                fb_name = _FALLBACK_TOOL.get(func_name)
                if fb_name and fb_name != func_name:
                    logger.info("Tools %s Retry后仍失败(%s)，尝试 Fallback: %s",
                                func_name, str(result.get("error", ""))[:80], fb_name)
                    fb_result = await toolkit.execute(fb_name, func_args)
                    if fb_result and isinstance(fb_result, dict):
                        fb_err = bool(fb_result.get("error"))
                        if not fb_err:
                            result = fb_result
                            _fallback_used = fb_name
                            logger.info("✅ Fallback %s → %s 成功", func_name, fb_name)
                        else:
                            logger.info("Fallback %s 也失败: %s", fb_name, str(fb_result.get("error", ""))[:100])

            _trace_elapsed = (time.time() - _trace_start) * 1000

            result_str = json.dumps(result, ensure_ascii=False)
            logger.info("Tools返回 %s: %s 字, 前300=%s", func_name, len(result_str), result_str[:300].replace("\n", " "))

            # ── Error检测 ──
            has_error = bool(result.get("error")) if isinstance(result, dict) else False

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

            # ── Error提示Injected ──
            if has_error and func_name in _tool_parameter_hints:
                err_text = str(result.get("error", ""))
                hint = _tool_parameter_hints[func_name]
                if hint not in err_text:
                    logger.info("Injected参数提示到Error消息")
                    result["error"] = f"{err_text}\n\n【参数提示】{hint}"
                    result_str = json.dumps(result, ensure_ascii=False)

            # ── Fallback 标记注入（让 LLM 知道发生了什么） ──
            if _fallback_used:
                note = f"【阶梯降级】工具 {func_name} 失败，自动 Fallback 到 {_fallback_used} 执行。"
                if isinstance(result.get("content"), str):
                    result["content"] = f"{note}\n\n{result['content']}"
                elif isinstance(result.get("error"), str):
                    result["error"] = f"{note} {result['error']}"
                result_str = json.dumps(result, ensure_ascii=False)

            return {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str[:TOOL_RESULT_MAX_CHARS],
            }

            # ── 门控⑦ 重复调用收敛检测：同工具+同参数 ≥3次 → 强制停止 ──
            _convergence_note = ""
            _call_key = (func_name, json.dumps(func_args, sort_keys=True, ensure_ascii=False)[:200])
            _call_count = self._tool_call_history.get(_call_key, 0) + 1
            self._tool_call_history[_call_key] = _call_count
            if _call_count >= 3:
                _convergence_note = (
                    f"\n\n[⚠️ 收敛警告：工具 {func_name} 已用相同参数调用 {_call_count} 次。"
                    f"请立即换方案或基于已有结果回答，不要再调此工具。]"
                )
                logger.warning("门控⑦ 重复调用收敛: %s 已调用 %s 次 (args=%s)",
                               func_name, _call_count, json.dumps(func_args, ensure_ascii=False)[:100])
            
            # ── 收敛警告即为注入文本 ──
            _budget_note = _convergence_note

            # ── ═══ 阶梯降级 L3: Escalate（Fallback 也失败，给 LLM 升级消息） ═══
            if has_error and _fallback_used is None and func_name not in _FALLBACK_TOOL:
                # 无可用 Fallback，在 Error 中注入替代建议
                # 不拦截，让 LLM 自行决定换方案
                pass

            # ── 门控⑥ 上下文保护：自动截断 + 留 recovery 通道 ──
            _protected = result_str
            if _ENABLE_CONTEXT_PROTECTION and len(_protected) > TOOL_RESULT_MAX_CHARS:
                _original_len = len(_protected)
                _summary_head = _protected[:1200]
                _protected = (
                    f"{_summary_head}\n\n"
                    f"[...工具结果自动截断: 原始 {_original_len} 字符，当前显示 "
                    f"{TOOL_RESULT_MAX_CHARS} 字符。如需查看完整内容，告诉我。]\n\n"
                    f"{_protected[-800:]}"
                ) + _budget_note
                logger.info("门控⑥ 上下文保护: %s = %s→%s 字符", func_name, _original_len, len(_protected))
            else:
                _protected = _protected[:TOOL_RESULT_MAX_CHARS] + _budget_note

            return {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _protected,
            }

        # ── Tools结果汇聚 + 递归 ──
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
