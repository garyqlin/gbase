# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/kernel.py

Kernel loop: call LLM → execute tools → repeat → reply.

Layer 2 of the 3-tier architecture:
- Does one thing: LLM call + tool_call execution loop
- Does NOT do: memory injection, experience storage, scout, cognition checks
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

logger = logging.getLogger(__name__)


def _is_retryable_error(result: dict) -> bool:
    """Determine whether a tool error is worth auto-retrying.
    Transient errors like network timeout, connection failure are retryable;
    Parameter errors, permission denied etc. are not retryable.
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


# ── RSI Dual-Knob: Task-aware Temperature ──
_TASK_TYPES: dict[str, list[str]] = {
    "explore": ["研究", "分析", "评估", "搜索", "对比", "方案", "proposal", "survey", "调研", "research", "analyze"],
    "execute": ["修改", "创建", "部署", "运行", "启动", "安装", "改", "执行", "添加", "删除",
                "edit", "create", "deploy", "run", "install", "commit", "push"],
    "discuss": ["你认为", "怎么看", "讨论", "建议", "意见", "反馈", "看法", "评价",
                "opinion", "feedback", "review", "suggestion"],
    "maintain": ["检查", "查看", "状态", "日志", "修复", "排查", "看下", "诊断",
                 "check", "status", "log", "diagnose", "inspect"],
}

_SHORT_EXECUTE: set[str] = {"重启", "部署", "推送", "发布", "回滚", "启动", "停止", "构建", "还原"}

_TEMP_CONFIG: dict[str, dict[str, object]] = {
    "explore":  {"mode": "warm",   "mirror_max": 8,  "experience_max": 3,  "desc": "exploratory"},
    "execute":  {"mode": "cold",   "mirror_max": 12, "experience_max": 5,  "desc": "strict"},
    "discuss":  {"mode": "warm",   "mirror_max": 6,  "experience_max": 2,  "desc": "standard"},
    "maintain": {"mode": "cold",   "mirror_max": 10, "experience_max": 4,  "desc": "cautious"},
}


def _classify_task_intent(message: str) -> str | None:
    """Classify user message into task type."""
    msg = message.strip()
    if not msg:
        return None
    if msg in _SHORT_EXECUTE:
        return "execute"
    if len(msg) < 10:
        return None
    if len(msg) > 200:
        return "explore"
    lower = msg.lower()
    for task_type, keywords in _TASK_TYPES.items():
        for kw in keywords:
            if kw in lower:
                return task_type
    return "discuss"


# Read from config.yaml; default to 15 if not present.
# Adjust by modifying config.yaml limits.max_tool_depth.
_NO_CONFIG = None
try:
    from main import _cfg_get

    _NO_CONFIG = False
except ImportError:
    _NO_CONFIG = True

if _NO_CONFIG:
    MAX_TOOL_DEPTH = 15
    # Circuit breaker config
    CIRCUIT_BREAKER = {
        "max_consecutive_failures": 2,  # Same tool fails 2 times in a row -> cooldown
        "max_round_failures": 5,  # Round total failures reach 5 -> circuit break & report
        "tool_cooldown_seconds": 60,  # Cooldown for 60 seconds
        "_failures": defaultdict(int),  # {tool_name: consecutive_fail_count}
        "_round_failure_count": 0,  # Cumulative failures this round
        "_cooldowns": {},  # {tool_name: unlock_timestamp}
        "_breaker_tripped": False,  # Whether round-level circuit breaker has triggered
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

"""Maximum allowed tool call nesting depth in a single run()."""

TOOL_BUDGET_WARN = 12
"""Tool call budget warning threshold. A reflection prompt is injected when this count is reached."""

# Tool parameter hints (injected on tool error to help LLM fix args)
_tool_parameter_hints = {
    "write_file": 'Parameter format: {"filepath": "/path/to/file", "content": "file content"}. '
    "filepath is a required file path, content is required file content. Do not pass empty object {}. ",
    "exec_command": 'Parameter format: {"command": "command to execute"}. command is a required string. Optional params: workdir, timeout. ',
    "read_file": 'Parameter format: {"filepath": "/path/to/file"}. Optional params: offset, max_chars. ',
}

TOOL_BUDGET_PLAN = 8
"""Beyond this count, the task is considered complex; next similar task should suggest planning first."""


class Kernel:
    """Opprime kernel."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str = "gpt-4o",
        system_prompt: str = "You are Opprime, an intelligent assistant.",
        temperature: float = 0.7,
        max_tokens: int = 32768,
        experience_engine: ExperienceEngine | None = None,
        skill_loader=None,
        mirror_engine: Mirror | None = None,
    ):
        self.client = client
        self.model = model
        self.base_system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.experience_engine = experience_engine
        self.skill_loader = skill_loader
        self.mirror_engine = mirror_engine

        # Register global context for tool functions to read
        from . import toolkit as tk

        tk.set_global("llm_client", client)
        tk.set_global("llm_model", model)
        if experience_engine:
            tk.set_global("experience_engine", experience_engine)
        if mirror_engine:
            tk.set_global("mirror_engine", mirror_engine)

        # ── RSI Dual-Knob: task type tracking ──
        self._current_task_type: str = "discuss"
        self._task_type_streak: int = 0
        # Triple-layer mirror filter: current user message for intent matching
        self._current_user_message: str = ""

    def _build_dynamic_system_prompt(self) -> str:
        """Dynamically build system prompt: base identity + workspace file injection + skill index.

        Rebuilt on each run() call, consistent with OpenClaw's per-turn reassembly logic.
        Assembly order follows OpenClaw's buildAgentSystemPrompt + CONTEXT_FILE_ORDER.
        """
        import os
        from datetime import datetime
        from pathlib import Path

        parts = [self.base_system_prompt]

        # Tool list injection
        from .toolkit import _tool_metadata, available_tools

        tools_list = available_tools()
        if tools_list:
            tool_lines = ["## Available Tools", ""]
            for tn in sorted(tools_list):
                meta = _tool_metadata.get(tn, {})
                desc = meta.get("description", "")[:80]
                tool_lines.append(f"- `{tn}`: {desc}")
            parts.append("\n".join(tool_lines))

        # Cloud: no workspace file injection (these files only exist on local Mac Studio)

        # Skill index injection
        # Follows Hermes dual-channel approach: system prompt only has name+description (index layer).
        # Full content is loaded on-demand by the LLM via read_file.
        if self.skill_loader:
            idx = self.skill_loader.get_skill_index()
            if idx:
                skill_lines = [
                    "## Available Skills (mandatory)",
                    "The following skills are available. Read their SKILL.md with `read_file` when needed.",
                    "",
                    "<available_skills>",
                ]
                for s in idx:
                    loc = os.path.join(str(self.skill_loader.skills_dir), s["name"], "SKILL.md")
                    triggers_str = "triggers: " + ", ".join(s["triggers"][:5]) if s["triggers"] else ""
                    desc = s["description"][:120]
                    skill_lines.append("  <skill>")
                    skill_lines.append(f"    <name>{s['name']}</name>")
                    skill_lines.append(f"    <description>{desc} [{triggers_str}]</description>")
                    skill_lines.append(f"    <location>{loc}</location>")
                    skill_lines.append("  </skill>")
                skill_lines.append("</available_skills>")
                skill_lines.append("")
                parts.append("\n".join(skill_lines))

        # Rule files injection
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
                    except Exception:
                        pass
                if rule_lines:
                    parts.append("\n---\n".join(rule_lines))

        # RSI Dual-Knob: task temperature based on detected task type
        temp_cfg = _TEMP_CONFIG.get(self._current_task_type, _TEMP_CONFIG["discuss"])

        # Mirror engine injection (count follows temperature mode)
        if self.mirror_engine:
            # ebbinghaus=True enables time-decay sorting (strength + frequency + last access time).
            # Recently & frequently used memories surface first; stale ones naturally sink but are never deleted.
            mirror_text = self.mirror_engine.get_injection_text(max_items=temp_cfg["mirror_max"], ebbinghaus=True, user_input=self._current_user_message or "")
            if mirror_text:
                parts.append(mirror_text)

        # Context handoff injection (fix AI amnesia: extract conversation essence from last session)
        if self.mirror_engine:
            handoff_text = self.mirror_engine.inject_last_context()
            if handoff_text:
                parts.append(handoff_text)

        # Time & timezone
        now = datetime.now()
        parts.append(
            f"## Current Date & Time\n"
            f"Time zone: Asia/Shanghai\n"
            f"Current time: {now.year}-{now.month:02d}-{now.day:02d} {now.hour:02d}:{now.minute:02d}\n"
        )

        # ── RSI Dual-Knob: Current Run Mode ──
        mode_desc = temp_cfg["desc"]
        mode_name = temp_cfg["mode"]
        parts.append(
            f"## Current Run Mode\n"
            f"Task type: {self._current_task_type} ({mode_desc})  |  "
            f"Mode: {mode_name}\n"
        )

        # Dynamic section (HEARTBEAT.md) placed after cache boundary
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
            except Exception:
                pass

        return "\n\n".join(parts)

    async def run(
        self,
        user_message: str,
        platform: str = "cli",
        session: JsonlSessionManager | None = None,
        max_seconds: int | None = None,
    ) -> str:
        """Single turn entry point.

        Flow:
        1. Dynamically assemble system prompt (workspace files + skill index + time)
        2. Build messages (system prompt + session context)
        3. Resolve tool list (platform + keyword routing)
        4. Call LLM -> tool loop -> reply
        5. Extract experience in background

        Args:
            user_message: User input
            platform: Platform identifier (cli / api)
            session: Optional SessionManager

        Returns:
            LLM final reply text
        """
        # Dynamically assemble system prompt
        self.system_prompt = self._build_dynamic_system_prompt()

        # Initialize trace
        import hashlib

        _trace_id = hashlib.md5((user_message + str(time.time())).encode()).hexdigest()[:12]
        init_trace(_trace_id, user_message[:100])

        # 1. Skill match injection (Hermes dual-channel approach)
        # system prompt already has skill index; clear old pre-injection logic,
        # let LLM decide whether to load full SKILL.md via read_file.
        enriched_message = user_message
        # Set current user message for triple-layer intent matching
        self._current_user_message = user_message or ""

        # 1.2 RSI Dual-Knob: task type detection
        detected = _classify_task_intent(user_message)
        if detected is not None:
            if detected == self._current_task_type:
                self._task_type_streak += 1
            else:
                self._task_type_streak = 0
                self._current_task_type = detected

        # 1.5 Search pre-execution
        # When user message contains search directive words, auto-search once before waiting for LLM decision.
        # Note: trigger words must not be too short (e.g. a single character), to avoid matching normal speech.
        pre_search = False
        search_query = user_message
        search_cues = ["look up", "search for", "find", "check online", "google"]
        # Hard requirement: the message must start or end with search intent to avoid false triggers
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
            # Append date constraint: if original message contains time words like "latest/today/recent",
            # automatically append current year and month.
            time_cues = ["latest", "today", "recent", "this month", "current", "new"]
            if any(cue in search_query.lower() for cue in time_cues):
                from datetime import datetime as _dt

                now = _dt.now()
                month_str = f"{now.year}-{now.month:02d}"
                if str(now.year) not in query:
                    query = query + " " + month_str
                elif str(now.month) not in query:
                    query = query + f" {now.month:02d}"
            if query:
                logger.info("Search pre-execution: query=%s", query)
                try:
                    search_result = await search_web(query=query, engines="bing_cn,duckduckgo,qwant,sogou")
                    if search_result and isinstance(search_result, dict):
                        from datetime import datetime as _dt

                        _now = _dt.now()
                        enriched_message = (
                            f"Current time is {_now.year}-{_now.month:02d}-{_now.day:02d} {_now.hour:02d}:{_now.minute:02d} (Beijing time, Asia/Shanghai).\n"
                            f"\n"
                            f"[Preliminary search reference] (This is a quick initial search result, may not be comprehensive or up-to-date. You may decide whether further searching is needed.)\n"
                            f"{json.dumps(search_result, ensure_ascii=False)[:4000]}\n\n"
                            f"---\n\n"
                            f"{enriched_message}"
                        )
                        logger.info("Search pre-execution completed")
                except Exception as e:
                    logger.warning("Search pre-execution failed (does not affect main flow): %s", e)

        # 2. Build messages
        messages: list[dict] = []
        if session:
            context = session.build_context()
            messages.extend(context)
            session.append_user_message(enriched_message)
        messages.append({"role": "user", "content": enriched_message})

        # Fast path: simple chat bypasses full tool chain
        tools = None
        if self._is_simple_chat(enriched_message):
            logger.info("Fast path: simple chat, skipping tool chain")
            reply = await self._fast_llm_call(messages)
            return reply

            # 4. Tool routing
        tools = toolkit.resolve_tools(platform, enriched_message)

        # 5. First LLM call (with timeout protection)
        _loop_coro = self._loop(messages, tools, depth=0, session=session)
        timeout_happened = False
        if max_seconds:
            try:
                reply = await asyncio.wait_for(_loop_coro, timeout=max_seconds)
            except TimeoutError:
                timeout_happened = True  # noqa: F841
                reply = f"[System] Task interrupted due to timeout ({max_seconds}s limit)"
                logger.warning("kernel.run timed out (%ds), reply truncated", max_seconds)
        else:
            reply = await _loop_coro

        # 6. Experience extraction (await ensures write completes before return)
        if self.experience_engine:
            tc_count = len([m for m in messages if m.get("role") == "tool"])
            try:
                await self.experience_engine.extract(
                    user_message=user_message,
                    reply=reply,
                    tool_calls_count=tc_count,
                    has_api_error=("error" in reply.lower() if reply else False) or (tc_count >= TOOL_BUDGET_WARN),
                    llm_client=self.client,
                )
            except Exception as e:
                logger.warning("Experience extraction exception (does not affect reply): %s", e)

        # 6. Close trace
        failure = get_failure_analysis()
        if failure and failure.get("has_failure"):
            close_trace(status="failed", error=failure["suggestion"])
        else:
            close_trace(status="completed")

        return reply

    def _is_simple_chat(self, message: str) -> bool:
        """Determine if this is a simple chat (fast path, bypasses tool chain)."""
        msg = message.strip()
        # Messages with tool-related keywords do not go through fast path
        tool_keywords = ["card", "search", "lookup", "analyze", "generate", "send", "write file", "execute", "tool"]
        for kw in tool_keywords:
            if kw in msg:
                return False
        # Short greeting/confirmation/question -- only messages under 5 characters qualify as short
        if len(msg) < 5:
            return True
        # Common simple patterns
        simple_patterns = [
            "嗯", "好", "明白", "继续", "可以",
            "在吗", "在不在",
            "hello", "hi",
            "谢谢", "好的", "收到",
            "ok", "okay",
            "是的", "对", "没错",
        ]
        msg_lower = msg.lower()
        return any(msg_lower == p or msg_lower.startswith(p) for p in simple_patterns)

    async def _fast_llm_call(self, messages: list) -> str:
        """Fast LLM call: no tools, single turn, no session writes."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=2048,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""

    async def _loop(
        self, messages: list[dict], tools: list[dict], depth: int = 0, session: JsonlSessionManager | None = None
    ) -> str:
        """Kernel recursive loop."""

        # Reset round-level circuit breaker state at the start of each loop
        if depth == 0:
            CIRCUIT_BREAKER["_failures"] = defaultdict(int)
            CIRCUIT_BREAKER["_round_failure_count"] = 0
            CIRCUIT_BREAKER["_breaker_tripped"] = False
            # Clean up expired cooldowns
            now = time.time()
            CIRCUIT_BREAKER["_cooldowns"] = {k: v for k, v in CIRCUIT_BREAKER["_cooldowns"].items() if v > now}

        if depth >= MAX_TOOL_DEPTH:
            # Limit exceeded: have LLM summarize and answer based on collected info, no more tool calls
            logger.info("Max tool depth %s reached, summarizing from existing info", MAX_TOOL_DEPTH)
            final_response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            "Above is the information you have gathered via tools. "
                            "Please answer the user's original question based on this information. "
                            "If insufficient, honestly state what was found and what was not. "
                            "Do not ask the user what to do next."
                        ),
                    },
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            choice0 = final_response.choices[0]
            reply = choice0.message.content or ""
            finish_reason = getattr(choice0, "finish_reason", None)
            if finish_reason == "length":
                logger.warning("LLM output truncated (finish_reason=length)! max_tokens=%s may be insufficient", self.max_tokens)
                reply += "\n\n[Output truncated, result may be incomplete]"
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
            logger.warning("LLM output truncated (finish_reason=length)! max_tokens=%s may be insufficient", self.max_tokens)
        msg = choice.message

        # DeepSeek V4 reasoning model -- must echo back reasoning_content
        _reasoning = None
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            _reasoning = msg.reasoning_content
        elif hasattr(msg, "model_extra") and msg.model_extra:
            _reasoning = msg.model_extra.get("reasoning_content")

        # Plain text reply
        if not msg.tool_calls:
            content = msg.content or ""
            asst = {"role": "assistant", "content": content}
            if _reasoning:
                asst["reasoning_content"] = _reasoning
            if session:
                session.append(asst)
            return content

        # Has tool_calls
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

        # Execute tools in parallel (independent tools at the same level run concurrently)
        async def _run_one_tool(tc):
            """Execute a single tool and return the tool message.

            Built-in circuit breaker protection:
            - Same tool fails 2 times consecutively -> cooldown that tool for 60 seconds
            - Round total failures reach 5 -> circuit break & report
            """
            func_name = tc.function.name
            try:
                func_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                func_args = {}

            # Circuit check: is the tool in cooldown?
            now = time.time()
            cooldown_until = CIRCUIT_BREAKER["_cooldowns"].get(func_name, 0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now)
                logger.info("Tool %s is in cooldown (%ds remaining), skipping", func_name, remaining)
                return {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"[Circuit break] Tool {func_name} hit consecutive failure limit, paused for {remaining}s. Consider switching tools or simplifying parameters.",
                }

            # Circuit check: has the round breaker been tripped?
            if CIRCUIT_BREAKER["_breaker_tripped"]:
                logger.info("Round circuit breaker tripped, tool %s skipped", func_name)
                return {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "[Circuit break] This round was terminated due to too many consecutive failures (round breaker tripped). Consider rethinking execution strategy.",
                }

            logger.info("Tool call: %s(%s)", func_name, json.dumps(func_args, ensure_ascii=False)[:120])

            # Auto-retry (max 1 retry)
            _trace_start = time.time()
            result = None
            for _retry in range(2):
                result = await toolkit.execute(func_name, func_args)
                if isinstance(result, dict) and _is_retryable_error(result):
                    logger.warning("Tool %s returned error, auto-retrying: %s", func_name, str(result.get("error", ""))[:200])
                    continue
                break
            _trace_elapsed = (time.time() - _trace_start) * 1000

            result_str = json.dumps(result, ensure_ascii=False)
            logger.info("Tool returned %s: %s chars, first 300=%s", func_name, len(result_str), result_str[:300].replace("\n", " "))

            # Error hint injection
            has_error = "error" in result if isinstance(result, dict) else False
            if has_error and func_name in _tool_parameter_hints:
                err_text = str(result.get("error", ""))
                hint = _tool_parameter_hints[func_name]
                if hint not in err_text:
                    logger.info("Injecting parameter hint into error message")
                    result["error"] = f"{err_text}\n\n[Parameter hint] {hint}"
                    result_str = json.dumps(result, ensure_ascii=False)

            # Circuit break: update concurrent failure counters
            has_error = "error" in result if isinstance(result, dict) else False
            if has_error:
                CIRCUIT_BREAKER["_failures"][func_name] += 1
                CIRCUIT_BREAKER["_round_failure_count"] += 1
                consecutive = CIRCUIT_BREAKER["_failures"][func_name]
                if consecutive >= CIRCUIT_BREAKER["max_consecutive_failures"]:
                    cooldown = CIRCUIT_BREAKER["tool_cooldown_seconds"]
                    CIRCUIT_BREAKER["_cooldowns"][func_name] = time.time() + cooldown
                    CIRCUIT_BREAKER["_failures"][func_name] = 0  # Reset; don't count during cooldown
                    logger.warning("Tool %s failed %d times consecutively, cooldown %ds", func_name, consecutive, cooldown)
                if CIRCUIT_BREAKER["_round_failure_count"] >= CIRCUIT_BREAKER["max_round_failures"]:
                    CIRCUIT_BREAKER["_breaker_tripped"] = True
                    logger.warning("Round circuit breaker tripped! Cumulative failures: %d", CIRCUIT_BREAKER["_round_failure_count"])
                    result["_breaker_tripped"] = True  # Mark in result
            else:
                # Reset failure counter for this tool on success
                CIRCUIT_BREAKER["_failures"][func_name] = 0

            # Trace recording
            record_tool_call(
                step=depth + 1,
                tool_name=func_name,
                input_digest=json.dumps(func_args, ensure_ascii=False)[:200],
                output_digest=result_str[:200],
                status="error" if has_error else "ok",
                error=str(result.get("error", ""))[:500] if has_error else "",
                duration_ms=_trace_elapsed,
            )

            # Truncation
            _content = result_str
            if len(_content) > 10000:
                _content = (
                    result_str[:10000]
                    + "\n\n[...Truncated: full result "
                    + str(len(result_str))
                    + " chars, showing first 10000 chars]"
                )
            return {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _content,
            }

        # Execute all tools in parallel
        tool_tasks = [_run_one_tool(tc) for tc in msg.tool_calls]
        tool_results = await asyncio.gather(*tool_tasks)

        # Append in original order (asyncio.gather preserves order)
        for tool_msg in tool_results:
            messages.append(tool_msg)
            if session:
                session.append(tool_msg)

        # Recurse (next LLM round)
        return await self._loop(messages, tools, depth + 1, session=session)
