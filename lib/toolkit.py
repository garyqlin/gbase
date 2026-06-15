# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/toolkit.py

工具注册系统：
- @tool 装饰器自动注册
- toolsets 关键词路由
- platform_map 平台白名单过滤

来自 V0，语义保留，代码精简。
"""

import asyncio
import hashlib
import inspect


# ── 统一返回协议 ────────────────────────────────────────
def _standard_return(ok: bool, data: str = "", error: str = "") -> dict:
    """所有工具调用最终输出的标准格式。

    统一约定（2026-06-06）：
      成功 -> {"ok": true, "result": "..."}
      失败 -> {"ok": false, "error": "..."}

    任何工具（feishu.py 通道层、工具层 @tool 函数）
    都应遵循这个格式，避免 kernel execute_tool 误解。
    """
    if ok:
        return {"ok": True, "result": data or ""}
    return {"ok": False, "error": error or "未知错误"}


import json
import logging
import time

logger = logging.getLogger(__name__)


# ── GMem: P1 HotCache ──────────────────────────────────
# P0: KV Cache 模式观察器
_PATTERN_COUNTER: dict[str, int] = {}
"""{cache_key: call_count} — 记录高频重复模式，为 KV Cache 复用做准备"""
_MAX_PATTERN_TRACK = 200

_MAX_CACHE_ENTRIES = 100
_HOT_CACHE: dict[str, tuple[float, dict, float]] = {}
"""{key: (expire_at, result, last_access)}"""


def hot_pattern_observe(tool_name: str, args: dict):
    """观察并记录高频工具调用模式。

    P0: 为后续 KV Cache 复用做准备——追踪哪些 (tool, args) 组合出现最频繁。
    """
    key = _hot_cache_key(tool_name, args)
    _PATTERN_COUNTER[key] = _PATTERN_COUNTER.get(key, 0) + 1
    # 限制追踪数量
    if len(_PATTERN_COUNTER) > _MAX_PATTERN_TRACK:
        # 保留调用次数最多的，按 max(50, _MAX_PATTERN_TRACK // 2) 缩容
        sorted_keys = sorted(_PATTERN_COUNTER.items(), key=lambda x: -x[1])
        _PATTERN_COUNTER.clear()
        retention = max(50, _MAX_PATTERN_TRACK // 2)
        for k, v in sorted_keys[:retention]:
            _PATTERN_COUNTER[k] = v


def hot_pattern_stats(top_n: int = 10) -> list[dict]:
    """返回高频模式统计。"""
    sorted_items = sorted(_PATTERN_COUNTER.items(), key=lambda x: -x[1])
    results = []
    for key, count in sorted_items[:top_n]:
        tool_name = key.split(":")[0] if ":" in key else key
        results.append({"key": key, "tool": tool_name, "count": count})
    return results


def _hot_cache_key(tool_name: str, args: dict) -> str:
    """生成缓存键：tool_name + args 的确定性 hash。"""
    arg_str = json.dumps(args, sort_keys=True, ensure_ascii=False)
    h = hashlib.md5(arg_str.encode()).hexdigest()[:16]
    return f"{tool_name}:{h}"


def _evict_oldest():
    """缓存超过上限时淘汰最旧的 20%。"""
    if len(_HOT_CACHE) <= _MAX_CACHE_ENTRIES:
        return
    sorted_items = sorted(_HOT_CACHE.items(), key=lambda x: x[1][2])  # 按 last_access 排序
    to_remove = sorted_items[: max(1, len(sorted_items) // 5)]  # 淘汰最旧的 20%
    for k, _ in to_remove:
        _HOT_CACHE.pop(k, None)


def hot_cache_set(tool_name: str, args: dict, result: dict, ttl: float = 300.0):
    """写入工具结果缓存。

    Args:
        tool_name: 工具名
        args: 工具参数（用作缓存键）
        result: 工具结果
        ttl: 有效期（秒），默认 5 分钟
    """
    now = time.time()
    key = _hot_cache_key(tool_name, args)
    _HOT_CACHE[key] = (now + ttl, result, now)
    _evict_oldest()


def hot_cache_get(tool_name: str, args: dict) -> dict | None:
    """查询工具缓存。命中且未过期则返回缓存结果，否则 None。

    Args:
        tool_name: 工具名
        args: 工具参数
    Returns:
        缓存的结果或 None
    """
    key = _hot_cache_key(tool_name, args)
    entry = _HOT_CACHE.get(key)
    if entry is None:
        return None
    expire_at, result, _ = entry
    now = time.time()
    if now >= expire_at:
        _HOT_CACHE.pop(key, None)
        return None
    # 更新 last_access
    _HOT_CACHE[key] = (expire_at, result, now)
    return result


def hot_cache_clear(tool_name: str = ""):
    """清空 HotCache。指定 tool_name 则只清空该工具的缓存。"""
    if not tool_name:
        _HOT_CACHE.clear()
        return
    keys_to_delete = [k for k in _HOT_CACHE if k.startswith(f"{tool_name}:")]
    for k in keys_to_delete:
        _HOT_CACHE.pop(k, None)


def hot_cache_stats() -> dict:
    """HotCache 统计信息。"""
    now = time.time()
    valid = sum(1 for v in _HOT_CACHE.values() if v[0] > now)
    return {
        "total": len(_HOT_CACHE),
        "valid": valid,
        "expired": len(_HOT_CACHE) - valid,
        "max_entries": _MAX_CACHE_ENTRIES,
    }


# ── 全局注册表 ──────────────────────────────────────────

_tool_registry: dict[str, callable] = {}
_tool_health_issues: list[str] = []  # 工具注册/验证过程中发现的问题
"""{tool_name: async_function}"""

_tool_metadata: dict[str, dict] = {}
"""{tool_name: {name, description, parameters}}"""

_toolsets: dict[str, dict] = {}
"""{
    "toolset_name": {
        "keywords": ["触发词", ...],
        "tools": ["tool_name", ...]
    }
}"""

_platform_map: dict[str, list[str]] = {}
"""{
    "platform_name": ["toolset_name", ...]
}"""

# ── 全局上下文（工具可读） ──────────────────────────────

_globals: dict = {}
"""工具可以读取的全局上下文。
由 main.py 在初始化时设置。
"""


def set_global(key: str, value):
    """设置一个全局值供工具读取。"""
    _globals[key] = value


def get_global(key: str, default=None):
    """读取一个全局值。"""
    return _globals.get(key, default)


# ── @tool 装饰器 ────────────────────────────────────────


def tool(name: str = "", description: str = "", parameters: dict | None = None):
    """工具注册装饰器。

    用法：
        @tool()
        async def get_weather(city: str):
            '''查天气'''
            ...

    自动从函数签名推导 parameters（OpenAI tool format）。
    """

    def decorator(func):
        nonlocal name, description, parameters
        tool_name = name or func.__name__

        if not description:
            description = (func.__doc__ or "").strip()

        if not parameters:
            # 从函数签名推断
            sig = inspect.signature(func)
            props = {}
            required = []
            for pname, param in sig.parameters.items():
                if pname in ("self", "cls"):
                    continue
                if param.default is inspect.Parameter.empty:
                    required.append(pname)
                ptype = "string"
                if param.annotation is not inspect.Parameter.empty:
                    type_map = {
                        str: "string",
                        int: "integer",
                        float: "number",
                        bool: "boolean",
                        list: "array",
                        dict: "object",
                    }
                    ptype = type_map.get(param.annotation, "string")
                props[pname] = {"type": ptype, "description": ""}
            parameters = {
                "type": "object",
                "properties": props,
                "required": required,
            }

        _tool_registry[tool_name] = func
        _tool_metadata[tool_name] = {
            "name": tool_name,
            "description": description,
            "parameters": parameters,
        }

        logger.debug("工具注册: %s", tool_name)
        return func

    return decorator


# ── 工具集注册 ──────────────────────────────────────────


def register_toolset(name: str, keywords: list[str], tools: list[str]):
    """注册一个工具集（Intent-based routing 的 Intent/能力组）。"""
    _toolsets[name] = {
        "keywords": [kw.lower() for kw in keywords],
        "tools": tools,
    }


def register_platform_map(platform: str, toolsets: list[str]):
    """注册平台到工具集的映射。"""
    _platform_map[platform] = toolsets


# ── GMem P0: 搜索结果异步沉淀 ──
_GMEM_SEARCH_DEPTH = 0
"""跟踪当前搜索深度，供搜索结果沉淀使用。"""


async def _async_record_search(mirror, query: str, _tool_name: str, args: dict):
    """后台保存搜索结果到 mirror。"""
    global _GMEM_SEARCH_DEPTH
    try:
        summary = str(args.get("query", "") or args.get("url", ""))[:200]
        _GMEM_SEARCH_DEPTH += 1
        mirror.record_search(query, summary, _GMEM_SEARCH_DEPTH)
    except Exception as e:
        logger.debug("GMem 搜索沉淀跳过: %s", e)


# ── 工具执行 ────────────────────────────────────────────


async def execute(tool_name: str, args: dict, use_cache: bool = False) -> dict:
    """执行工具。

    Args:
        tool_name: 工具名
        args: 工具参数
        use_cache: 是否启用 HotCache（仅读操作建议启用）
    """
    # GMem P0: 模式观察（跟踪高频调用）
    hot_pattern_observe(tool_name, args)

    # GMem P0: 搜索结果自动沉淀到 memory
    search_tools = {
        "anysearch_search",
        "anysearch_batch_search",
        "anysearch_extract",
        "honeycomb_search",
        "search_web",
        "fetch_page",
    }
    if tool_name in search_tools:
        try:
            mirror = globals().get("_GMEM_MIRROR")
            if mirror and hasattr(mirror, "record_search"):
                query = str(args.get("query", "") or args.get("url", "") or tool_name)[:100]
                asyncio.ensure_future(_async_record_search(mirror, query, tool_name, args))
        except Exception:
            pass

    # GMem P1: HotCache 查缓存
    if use_cache:
        cached = hot_cache_get(tool_name, args)
        if cached is not None:
            return cached

    func = _tool_registry.get(tool_name)
    if not func:
        return _standard_return(False, error=f"未知工具: {tool_name}")
    try:
        if asyncio.iscoroutinefunction(func):
            result = await func(**args)
        else:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: func(**args))
        if result is None:
            return _standard_return(False, error=f"工具 {tool_name} 返回 None")
        if isinstance(result, dict):
            # GMem P1: HotCache 写缓存（仅成功的读操作）
            if use_cache and result.get("error") is None:
                hot_cache_set(tool_name, args, result)
            # 标准化输出：已有的 dict 尽量保留内容，但确保有 ok 字段
            if "ok" not in result:
                result["ok"] = result.get("error") is None
            if result.get("error"):
                result["ok"] = False
            return result
        return _standard_return(True, data=str(result))
    except Exception as e:
        logger.error("工具执行失败 %s: %s", tool_name, e)
        return _standard_return(False, error=f"工具执行失败: {str(e)}")


# ── 工具路由 ────────────────────────────────────────────


def resolve_tools(platform: str, user_message: str | list | dict) -> list[dict]:
    """根据平台和用户消息关键词，解析可用的工具定义列表（OpenAI format）。

    流程：
    1. 根据 platform 从 platform_map 获取允许的工具集名
    2. 遍历工具集，检查用户消息中的关键词
    3. 匹配到的工具集的工具 + 保底工具集的工具
    4. 返回 OpenAI tool format 的列表
    """
    allowed_toolsets = _platform_map.get(platform, [])
    if not allowed_toolsets:
        # 没有平台限制，给所有工具
        return _all_tool_defs()

    matched_tools: set[str] = set()

    # 多模态消息（list[dict]）转文本用于关键词匹配
    if isinstance(user_message, list):
        msg_text = ""
        for item in user_message:
            if isinstance(item, dict):
                msg_text += item.get("text", "") or str(item.get("image_url", ""))
            else:
                msg_text += str(item)
    elif isinstance(user_message, dict):
        msg_text = str(user_message)
    else:
        msg_text = user_message
    user_lower = msg_text.lower()

    for ts_name in allowed_toolsets:
        ts = _toolsets.get(ts_name)
        if not ts:
            continue
        # 检查关键词是否匹配
        for kw in ts["keywords"]:
            if kw in user_lower:
                matched_tools.update(ts["tools"])
                break
        # 没匹配上的，如果工具集有关键词"*"（通配），也加上
        if "*" in ts.get("keywords", []):
            matched_tools.update(ts["tools"])

    if not matched_tools:
        # 没有任何关键词匹配，给 chat 工具集（对话辅助）
        chat_ts = _toolsets.get("chat", {})
        matched_tools = set(chat_ts.get("tools", []))

        # 加上保底：web 工具集
        web_ts = _toolsets.get("web", {})
        matched_tools.update(web_ts.get("tools", []))

    # 转成 OpenAI tool format
    defs = []
    for name in matched_tools:
        meta = _tool_metadata.get(name)
        if meta:
            defs.append(
                {
                    "type": "function",
                    "function": {
                        "name": meta["name"],
                        "description": meta["description"],
                        "parameters": meta["parameters"],
                    },
                }
            )

    return defs


def get_platform_toolsets(platform: str) -> list[dict]:
    """获取平台允许的完整工具集定义（不按关键词缩小范围）。"""
    allowed = _platform_map.get(platform, [])
    return [_toolsets.get(ts, {}) for ts in allowed]


def _all_tool_defs() -> list[dict]:
    defs = []
    for _name, meta in _tool_metadata.items():
        defs.append(
            {
                "type": "function",
                "function": {
                    "name": meta["name"],
                    "description": meta["description"],
                    "parameters": meta["parameters"],
                },
            }
        )
    return defs


# ── 工具文件自动扫描 ────────────────────────────────────


def auto_scan(path: str = "tools"):
    """自动扫描 tools/ 目录下的所有 .py 文件并 import（触发 @tool 延迟注册）。

    企业模式 (2026-06-15): 加载后验证每个工具 callable + schema 完整性，
    记录问题到 _tool_health_issues 供 /health 暴露。
    """
    import importlib
    import os

    global _tool_health_issues
    _tool_health_issues = []

    tools_dir = path
    if not os.path.isdir(tools_dir):
        _tool_health_issues.append(f"工具目录不存在: {tools_dir}")
        logger.warning("工具目录不存在: %s", tools_dir)
        return

    # 扫描目录下的所有 Python 文件
    for fname in sorted(os.listdir(tools_dir)):
        if fname.endswith(".py") and not fname.startswith("_"):
            mod_name = fname[:-3]
            spec = importlib.util.spec_from_file_location(mod_name, os.path.join(tools_dir, fname))
            if spec and spec.loader:
                try:
                    spec.loader.exec_module(importlib.util.module_from_spec(spec))
                    logger.debug("加载工具文件: %s", fname)
                except Exception as e:
                    _tool_health_issues.append(f"导入失败 {fname}: {e}")
                    logger.warning("加载工具文件失败 %s: %s", fname, e)
            else:
                _tool_health_issues.append(f"无法加载 spec: {fname}")

    # 加载后验证：每个已注册工具必须 callable + schema 完整
    _validate_tool_registry()


# ── 工具定义查询 ────────────────────────────────────────


def _validate_tool_registry():
    """企业级验证：每个已注册工具 callable + schema 检测。

    仅检查硬性错误（不可调用/schema缺失），不检查无参数（无参工具合法）。
    """
    global _tool_health_issues
    for name, func in sorted(_tool_registry.items()):
        if not callable(func):
            _tool_health_issues.append(f"工具不可调用: {name}")
            continue
        meta = _tool_metadata.get(name, {})
        if not meta:
            _tool_health_issues.append(f"工具缺少元数据: {name}")
            continue
        params = meta.get("parameters", {})
        if not params or "type" not in params:
            _tool_health_issues.append(f"工具 schema 不完整: {name}")


def get_tool_health() -> dict:
    """返回工具系统健康报告。"""
    return {
        "total_registered": len(_tool_registry),
        "health_issues": len(_tool_health_issues),
        "issues": _tool_health_issues[:20],  # 最多 20 条
    }


def get_tool_registry_keys() -> list[str]:
    return sorted(_tool_registry.keys())


def available_tools() -> list[str]:
    return list(_tool_registry.keys())


def get_tool_metadata(name: str) -> dict | None:
    return _tool_metadata.get(name)


# ── 能力目录（语义化注入） ────────────────────────────────

_CAPABILITY_REGISTRY: dict[str, list[str]] = {}
"""
能力目录：{能力分类: [工具名, ...]}

自动从 _tool_metadata 的 description 中识别归类。
之所以不走硬编码是因为工具是自动注册的，加新工具自动归入对应类别。
"""

# 能力分类关键词映射
_CAPABILITY_MAP: dict[str, set[str]] = {
    "文档生成": {"gen_pdf", "gen_docx", "gen_pptx", "gen_xlsx", "ocr_pdf"},
    "文档加工": {"yf_create_ppt", "author_doc", "author_test_plan"},
    "图像视觉": {
        "analyze_image",
        "ocr_image",
        "vision",
        "yf_generate_image",
        "yf_create",
        "yf_recognize",
        "vision_inspect",
        "vision_local",
    },
    "搜索检索": {"search", "fetch", "anysearch", "honeycomb", "note_search"},
    "文件操作": {"read_file", "write_file", "file_", "my_path", "note_write"},
    "命令执行": {"exec_command"},
    "消息通讯": {"send_feishu_card", "send_file", "send_mail", "mail", "check_inbox"},
    "学习记忆": {
        "learn",
        "memory",
        "mirror",
        "knowledge",
        "remember",
        "add_learn_topic",
        "list_learn_topics",
        "remove_learn_topic",
    },
    "编程工程": {
        "self_edit",
        "editor",
        "test",
        "verify",
        "distill",
        "scan_project",
        "forge_verify",
        "health_check",
        "anchor_keeper",
        "qa_",
    },
    "AI 进化": {"best_skill", "distill", "hive_mind", "optimize_prompt"},
}

# 各类别的说明文案
_CAPABILITY_BLURBS: dict[str, str] = {
    "文档生成": (
        "你可以直接生成 PDF 报告（大厂咨询风格，含图表/卡片/色块，支持10+配色主题）、"
        "Word(.docx) 文档、Excel(.xlsx) 表格（含公式支持）、PPT(.pptx) 演示文稿。"
        "生成后直接调 `send_file` 发送文件到飞书，无需人工转存。"
    ),
    "图像视觉": ("支持中文 OCR（本地/云端）、图像理解与分析（豆包VLM）、AI 图像生成（文生图）、视觉缺陷检测。"),
    "搜索检索": ("多引擎搜索引擎（AnySearch）、结构化知识库检索（Knowledge/Self）、笔记搜索、网页一键抓取转换。"),
    "消息通讯": ("飞书发送富文本卡片消息、邮箱收发件箱查询与发送。"),
    "学习记忆": ("可自主录入新知识（remember_fact/remember_info）、管理学习主题、自动知识老化去噪。"),
    "编程工程": ("代码编辑/重构/回滚、自动生成测试用例、黑盒冒烟测试、工程健康检查、项目结构扫描。"),
    "命令执行": ("直接执行 Shell 命令，可完成文件操作/服务管理/系统指令等。"),
    "文件操作": ("读取/写入任意本地文件，支持文件哈希校验与完整性验证。"),
    "AI 进化": ("经验蒸馏（从对话提取best practice）、蜂群思维（多视角交叉验证）、提示词自动优化。"),
}


def _classify_tools() -> dict[str, list[str]]:
    """根据 _tool_metadata 的 description 和名称，将工具自动归类到能力分类。

    每次调用时重建，确保添加新工具后自动归入正确类别。
    """
    categories: dict[str, list[str]] = {}
    used: set[str] = set()

    # 第一轮：按 _CAPABILITY_MAP 精确匹配
    for cat, patterns in _CAPABILITY_MAP.items():
        matched = []
        for name in sorted(_tool_registry):
            if name in used:
                continue
            for p in patterns:
                if name.startswith(p) or name.endswith(p):
                    matched.append(name)
                    break
        if matched:
            categories[cat] = matched
            used.update(matched)

    # 第二轮：剩余工具按 description 关键词
    for name in sorted(_tool_registry):
        if name in used:
            continue
        meta = _tool_metadata.get(name)
        if not meta:
            continue
        desc = (meta.get("description") or "").lower()
        # 尝试归类
        assigned = False
        for cat, patterns in _CAPABILITY_MAP.items():
            if cat in categories and name in categories[cat]:
                continue
            # 检查 description 中的关键词
            for p in patterns:
                p_clean = p.rstrip("_").replace("_", " ")
                if p_clean in desc and len(p_clean) > 3:
                    if cat not in categories:
                        categories[cat] = []
                    categories[cat].append(name)
                    used.add(name)
                    assigned = True
                    break
            if assigned:
                break

    # 第三轮：未归类的放"其他工具"
    unassigned = [n for n in sorted(_tool_registry) if n not in used]
    if unassigned:
        categories["其他工具"] = unassigned

    return categories


def tool_list_compact() -> str:
    """按能力分类生成语义化工具清单，用于 system prompt 注入。

    每次调用动态重建。加新工具后自动归类到正确的能力类别。
    格式对标 OpenClaw 的 `<available_skills>` 模式：
    LLM 一眼知道每个分类能干什么，而不是看工具名列表。
    """
    if not _tool_registry:
        return ""

    categories = _classify_tools()
    lines = [f"## 可用能力 ({len(_tool_registry)} 个工具)", ""]
    lines.append("以下是你的全部能力。根据当前任务选择合适的工具，工具 schema 由系统自动解析。")
    lines.append("")

    for cat in sorted(categories):
        tools = categories[cat]
        tools_str = ", ".join(tools)
        blurb = _CAPABILITY_BLURBS.get(cat, "")
        if blurb:
            lines.append(f"### {cat}")
            lines.append(f"{blurb}")
            lines.append(f"工具: `{tools_str}`")
        else:
            lines.append(f"### {cat}")
            lines.append(f"工具: `{tools_str}`")
        lines.append("")

    lines.append("---")
    lines.append("需要某个能力时直接调用对应工具即可，无需提前加载。")
    return "\n".join(lines)
