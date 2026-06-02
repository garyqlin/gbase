# SPDX-License-Identifier: MIT
"""
gbase/lib/toolkit.py

工具注册系统：
- @tool 装饰器自动注册
- toolsets 关键词路由
- platform_map 平台白名单过滤

来自 V0，语义保留，代码精简。
"""

import asyncio
import hashlib
import inspect
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
        return {"error": f"未知工具: {tool_name}"}
    try:
        if asyncio.iscoroutinefunction(func):
            result = await func(**args)
        else:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: func(**args))
        if result is None:
            return {"error": f"工具 {tool_name} 返回 None"}
        if isinstance(result, dict):
            # GMem P1: HotCache 写缓存（仅成功的读操作）
            if use_cache and "error" not in result:
                hot_cache_set(tool_name, args, result)
            return result
        return {"result": str(result)}
    except Exception as e:
        logger.error("工具执行失败 %s: %s", tool_name, e)
        return {"error": f"工具执行失败: {str(e)}"}


# ── 工具路由 ────────────────────────────────────────────


def resolve_tools(platform: str, user_message: str) -> list[dict]:
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

    user_lower = user_message.lower()

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
    """自动扫描 tools/ 目录下的所有 .py 文件并 import（触发 @tool 延迟注册）。"""
    import importlib
    import os

    tools_dir = path
    if not os.path.isdir(tools_dir):
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
                    logger.warning("加载工具文件失败 %s: %s", fname, e)


# ── 工具定义查询 ────────────────────────────────────────


def available_tools() -> list[str]:
    return list(_tool_registry.keys())


def get_tool_metadata(name: str) -> dict | None:
    return _tool_metadata.get(name)


def tool_list_compact() -> str:
    """Generate a compact tool list for system prompt injection.

    Returns a single line with all tool names grouped by category,
    instead of expanding full descriptions and schemas.
    """
    if not _tool_registry:
        return ""
    # Group by category (first word of tool name, or first segment before _)
    from collections import defaultdict

    categories = defaultdict(list)
    for name in sorted(_tool_registry):
        # Extract category prefix
        prefix = name.split("_")[0] if "_" in name else name
        categories[prefix].append(name)
    lines = [f"## Available Tools ({len(_tool_registry)} total)", ""]
    for cat in sorted(categories):
        tools = categories[cat]
        lines.append(f"- {cat}: {', '.join(tools)}")
    lines.append("")
    lines.append("(Call any tool — schema is auto-resolved by the system)")
    return "\n".join(lines)
