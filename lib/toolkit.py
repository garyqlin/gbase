# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/toolkit.py

Tool registration system:
- @tool 装饰器自动注册
- toolsets 关键词路由
- platform_map 平台白名单过滤

来自 V0，语义保留，代码精简。
"""

import inspect
import logging

logger = logging.getLogger(__name__)


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


# ── 工具执行 ────────────────────────────────────────────

async def execute(tool_name: str, args: dict) -> dict:
    """执行工具。"""
    func = _tool_registry.get(tool_name)
    if not func:
        return {"error": f"未知工具: {tool_name}"}
    try:
        result = await func(**args)
        if result is None:
            return {"error": f"工具 {tool_name} 返回 None"}
        if isinstance(result, dict):
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
            defs.append({
                "type": "function",
                "function": {
                    "name": meta["name"],
                    "description": meta["description"],
                    "parameters": meta["parameters"],
                }
            })

    return defs


def get_platform_toolsets(platform: str) -> list[dict]:
    """获取平台允许的完整工具集定义（不按关键词缩小范围）。"""
    allowed = _platform_map.get(platform, [])
    return [_toolsets.get(ts, {}) for ts in allowed]


def _all_tool_defs() -> list[dict]:
    defs = []
    for name, meta in _tool_metadata.items():
        defs.append({
            "type": "function",
            "function": {
                "name": meta["name"],
                "description": meta["description"],
                "parameters": meta["parameters"],
            }
        })
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
            spec = importlib.util.spec_from_file_location(mod_name,
                                                          os.path.join(tools_dir, fname))
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
