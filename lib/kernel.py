# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/kernel.py

Kernel loop: call LLM → execute tools → repeat → reply.

三层架构的 Layer 2:
- 只做一件事:LLM 调用 + tool_call 执行循环
- 不做:记忆注入、经验存储、侦察兵、认知检测
- 最多 5 层工具调用深度
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


# 从 config.yaml 读取，不存在则默认 15
# 可通过修改 config.yaml limits.max_tool_depth 调整
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
        "max_consecutive_failures": 2,  # 同工具连续失败 2 次 → 暂停
        "max_round_failures": 5,  # 整轮失败 5 次 → 熔断上报
        "tool_cooldown_seconds": 60,  # 暂停 60 秒
        "_failures": defaultdict(int),  # {tool_name: consecutive_fail_count}
        "_round_failure_count": 0,  # 本轮累计失败数
        "_cooldowns": {},  # {tool_name: unlock_timestamp}
        "_breaker_tripped": False,  # 整轮熔断是否已触发
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
    "exec_command": '参数格式: {"command": "要执行的命令"}。command 是必填字符串。可选参数: workdir, timeout。',
    "read_file": '参数格式: {"filepath": "/path/to/file"}。可选参数: offset, max_chars。',
}

TOOL_BUDGET_PLAN = 8
"""超过此数视为复杂任务，下次同类任务应建议先规划。"""


class Kernel:
    """Opprime 内核。"""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str = "gpt-4o",
        system_prompt: str = "你是 Opprime,一个智能助手。",
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

        # 注册全局上下文供工具函数读取
        from . import toolkit as tk

        tk.set_global("llm_client", client)
        tk.set_global("llm_model", model)
        if experience_engine:
            tk.set_global("experience_engine", experience_engine)
        if mirror_engine:
            tk.set_global("mirror_engine", mirror_engine)

    def _build_dynamic_system_prompt(self) -> str:
        """动态构建 system prompt：基础身份 + workspace file injection + skill 索引。

        每次 run() 调用时重建，与 OpenClaw 每 turn 重新拼装的逻辑一致。
        拼装顺序参考 OpenClaw 的 buildAgentSystemPrompt + CONTEXT_FILE_ORDER。
        """
        import os
        from datetime import datetime
        from pathlib import Path

        parts = [self.base_system_prompt]

        # ── 工具列表注入 ──
        from .toolkit import _tool_metadata, available_tools

        tools_list = available_tools()
        if tools_list:
            tool_lines = ["## Available Tools", ""]
            for tn in sorted(tools_list):
                meta = _tool_metadata.get(tn, {})
                desc = meta.get("description", "")[:80]
                tool_lines.append(f"- `{tn}`: {desc}")
            parts.append("\n".join(tool_lines))

        # ── 云端：无 workspace 文件注入（这些文件只在本地 Mac Studio）

        # ── Skill 索引注入 ──
        # 参考 Hermes 双通道方案：system prompt 只放名称+描述（索引层）
        # 全量内容由 LLM 自行通过 read_file 按需加载
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
                    except Exception:
                        pass
                if rule_lines:
                    parts.append("\n---\n".join(rule_lines))

        # ── 鉴面引擎注入（从5条扩到8条，提高回忆覆盖率） ──
        if self.mirror_engine:
            # 启用 Ebbinghaus 遗忘曲线 + 3层衰减，默认 8 条注入
            # ebbinghaus=True 启用时间衰减排序（强度 + 频率 + 上次访问时间）
            # 让最近高频使用的记忆优先浮现，久不用的自然下沉但不删除
            mirror_text = self.mirror_engine.get_injection_text(max_items=8, ebbinghaus=True)
            if mirror_text:
                parts.append(mirror_text)

        # ── 上下文交接注入（修复 AI 失忆：从上次 session 提取对话实质） ──
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
        """单次对话入口。

        流程:
        1. 动态拼装 system prompt（workspace files + skill 索引 + 时间）
        2. 构建 messages（含 system prompt + session context）
        3. 解析工具列表（平台 + 关键词路由）
        4. 调用 LLM → 工具循环 → 回复
        5. 后台提取经验

        Args:
            user_message: 用户输入
            platform: Platform identifier (cli / api)
            session: 可选的 SessionManager

        Returns:
            LLM 最终回复文本
        """
        # ── 动态拼装 system prompt ──
        self.system_prompt = self._build_dynamic_system_prompt()

        # ── 初始化 trace ──
        import hashlib

        _trace_id = hashlib.md5((user_message + str(time.time())).encode()).hexdigest()[:12]
        init_trace(_trace_id, user_message[:100])

        # ── 1. Skill 匹配注入（Hermes 双通道方案） ──
        # system prompt 已有 skill 索引，这里清空旧预注入逻辑，
        # 改为 LLM 自行决定是否用 read_file 加载完整 SKILL.md
        enriched_message = user_message

        # ── 1.5 搜索预执行 ──
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
                logger.info("搜索预执行: query=%s", query)
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
                        logger.info("搜索预执行完成")
                except Exception as e:
                    logger.warning("搜索预执行失败（不影响主流程）: %s", e)

        # ── 2. 构建 messages ──
        messages: list[dict] = []
        if session:
            context = session.build_context()
            messages.extend(context)
            session.append_user_message(enriched_message)
        messages.append({"role": "user", "content": enriched_message})

        # ── 快速路径：简单对话不进完整工具链 ──
        tools = None
        if self._is_simple_chat(enriched_message):
            logger.info("快速路径: 简单对话，跳过工具链")
            reply = await self._fast_llm_call(messages)
            return reply

            # ── 4. 工具路由 ──
        tools = toolkit.resolve_tools(platform, enriched_message)

        # ── 5. 首次 LLM 调用（带超时保护）──
        _loop_coro = self._loop(messages, tools, depth=0, session=session)
        timeout_happened = False
        if max_seconds:
            try:
                reply = await asyncio.wait_for(_loop_coro, timeout=max_seconds)
            except TimeoutError:
                timeout_happened = True  # noqa: F841
                reply = f"[系统] 任务因超时中断（{max_seconds}秒限制）"
                logger.warning("kernel.run 超时（%d秒），已截断回复", max_seconds)
        else:
            reply = await _loop_coro

        # ── 6. 经验提取（await 保证写入完成后再返回）──
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
                logger.warning("经验提取异常（不影响回复）: %s", e)

        # ── 6. 关闭 trace ──
        failure = get_failure_analysis()
        if failure and failure.get("has_failure"):
            close_trace(status="failed", error=failure["suggestion"])
        else:
            close_trace(status="completed")

        return reply

    def _is_simple_chat(self, message: str) -> bool:
        """判断是否简单对话（快速路径，不进工具链）。"""
        msg = message.strip()
        # 带工具类关键词的不走快速路径
        tool_keywords = ["卡片", "搜索", "查", "分析", "生成", "发送", "写文件", "执行", "工具"]
        for kw in tool_keywords:
            if kw in msg:
                return False
        # 简短问候/确认/问题 - 5字以内才算简短
        if len(msg) < 5:
            return True
        # 常见简单模式
        simple_patterns = [
            "嗯",
            "好",
            "明白",
            "继续",
            "可以",
            "在吗",
            "在不在",
            "hello",
            "hi",
            "谢谢",
            "好的",
            "收到",
            "ok",
            "okay",
            "是的",
            "对",
            "没错",
        ]
        msg_lower = msg.lower()
        return any(msg_lower == p or msg_lower.startswith(p) for p in simple_patterns)

    async def _fast_llm_call(self, messages: list) -> str:
        """快速 LLM 调用：无工具，单轮，不写 session。"""
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

        # 并行执行工具（同层可独立工具同时跑）
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
                return {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"[熔断] 工具 {func_name} 因连续失败达到上限，暂停 {remaining} 秒后恢复。建议更换工具或简化参数。",
                }

            # ═══ 熔断检查：整轮是否已熔断 ═══
            if CIRCUIT_BREAKER["_breaker_tripped"]:
                logger.info("整轮熔断已触发，工具 %s 跳过", func_name)
                return {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "[熔断] 本轮执行因连续失败过多被终止（整轮熔断已触发），建议重新思考执行策略。",
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

            # ── 错误提示注入 ──
            has_error = "error" in result if isinstance(result, dict) else False
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
                    cooldown = CIRCUIT_BREAKER["tool_cooldown_seconds"]
                    CIRCUIT_BREAKER["_cooldowns"][func_name] = time.time() + cooldown
                    CIRCUIT_BREAKER["_failures"][func_name] = 0  # 重置，冷却期不计数
                    logger.warning("🔴 工具 %s 连续失败 %d 次，冷却 %ds", func_name, consecutive, cooldown)
                if CIRCUIT_BREAKER["_round_failure_count"] >= CIRCUIT_BREAKER["max_round_failures"]:
                    CIRCUIT_BREAKER["_breaker_tripped"] = True
                    logger.warning("🔴 整轮熔断触发！累计失败 %d 次", CIRCUIT_BREAKER["_round_failure_count"])
                    result["_breaker_tripped"] = True  # 标记到结果中
            else:
                # 成功后清零该工具的失败计数
                CIRCUIT_BREAKER["_failures"][func_name] = 0

            # ── trace 记录 ──
            record_tool_call(
                step=depth + 1,
                tool_name=func_name,
                input_digest=json.dumps(func_args, ensure_ascii=False)[:200],
                output_digest=result_str[:200],
                status="error" if has_error else "ok",
                error=str(result.get("error", ""))[:500] if has_error else "",
                duration_ms=_trace_elapsed,
            )

            # 截断
            _content = result_str
            if len(_content) > 10000:
                _content = (
                    result_str[:10000]
                    + "\n\n[...截断: 完整结果 "
                    + str(len(result_str))
                    + " 字符，仅展示前 10000 字符]"
                )
            return {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _content,
            }

        # 并行执行所有工具
        tool_tasks = [_run_one_tool(tc) for tc in msg.tool_calls]
        tool_results = await asyncio.gather(*tool_tasks)

        # 按原始顺序追加（asyncio.gather 保持顺序）
        for tool_msg in tool_results:
            messages.append(tool_msg)
            if session:
                session.append(tool_msg)

        # 递归(下一轮 LLM)
        return await self._loop(messages, tools, depth + 1, session=session)
