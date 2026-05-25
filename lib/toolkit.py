# SPDX-License-Identifier: MIT
"""
opprime-core-v2/lib/toolkit.py

Tool registration system:
- @tool decorator auto-registration
- toolsets keyword routing
- platform_map platform whitelist filtering

Migrated from V0. Semantics preserved, code streamlined.
"""

import inspect
import logging

logger = logging.getLogger(__name__)


# ── Global registry ───────────────────────────────────────

_tool_registry: dict[str, callable] = {}
"""{tool_name: async_function}"""

_tool_metadata: dict[str, dict] = {}
"""{tool_name: {name, description, parameters}}"""

_toolsets: dict[str, dict] = {}
"""{
    "toolset_name": {
        "keywords": ["trigger words", ...],
        "tools": ["tool_name", ...]
    }
}"""

_platform_map: dict[str, list[str]] = {}
"""{
    "platform_name": ["toolset_name", ...]
}"""

# ── Global context (readable by tools) ────────────────────

_globals: dict = {}
"""Global context readable by tools.
Set by main.py during initialization.
"""


def set_global(key: str, value):
    """Set a global value for tools to read."""
    _globals[key] = value


def get_global(key: str, default=None):
    """Read a global value."""
    return _globals.get(key, default)


# ── @tool decorator ───────────────────────────────────────

def tool(name: str = "", description: str = "", parameters: dict | None = None):
    """Tool registration decorator.

    Usage:
        @tool()
        async def get_weather(city: str):
            '''Look up weather'''
            ...

    Auto-infers parameters from function signature (OpenAI tool format).
    """

    def decorator(func):
        nonlocal name, description, parameters
        tool_name = name or func.__name__

        if not description:
            description = (func.__doc__ or "").strip()

        if not parameters:
            # Infer from function signature
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

        logger.debug("Tool registered: %s", tool_name)
        return func

    return decorator


# ── Toolset registration ──────────────────────────────────

def register_toolset(name: str, keywords: list[str], tools: list[str]):
    """Register a toolset (Intent/capability group for intent-based routing)."""
    _toolsets[name] = {
        "keywords": [kw.lower() for kw in keywords],
        "tools": tools,
    }


def register_platform_map(platform: str, toolsets: list[str]):
    """Register a platform-to-toolset mapping."""
    _platform_map[platform] = toolsets


# ── Tool execution ────────────────────────────────────────


async def execute(tool_name: str, args: dict) -> dict:
    """Execute a tool."""
    func = _tool_registry.get(tool_name)
    if not func:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        result = await func(**args)
        if result is None:
            return {"error": f"Tool {tool_name} returned None"}
        if isinstance(result, dict):
            return result
        return {"result": str(result)}
    except Exception as e:
        logger.error("Tool execution failed %s: %s", tool_name, e)
        return {"error": f"Tool execution failed: {str(e)}"}


# ── Tool routing ──────────────────────────────────────────


def resolve_tools(platform: str, user_message: str) -> list[dict]:
    """Resolve available tool definitions (OpenAI format) based on platform and user message keywords.

    Flow:
    1. Get allowed toolsets from platform_map by platform
    2. Iterate toolsets, check keywords in user message
    3. Include matched tools + fallback toolsets' tools
    4. Return list in OpenAI tool format
    """
    allowed_toolsets = _platform_map.get(platform, [])
    if not allowed_toolsets:
        # No platform restriction, return all tools
        return _all_tool_defs()

    matched_tools: set[str] = set()

    user_lower = user_message.lower()

    for ts_name in allowed_toolsets:
        ts = _toolsets.get(ts_name)
        if not ts:
            continue
        # Check if keyword matches
        for kw in ts["keywords"]:
            if kw in user_lower:
                matched_tools.update(ts["tools"])
                break
        # For unmatched toolsets, if they have keyword "*" (wildcard), include them too
        if "*" in ts.get("keywords", []):
            matched_tools.update(ts["tools"])

    if not matched_tools:
        # No keyword matched, return chat toolset (conversation helper)
        chat_ts = _toolsets.get("chat", {})
        matched_tools = set(chat_ts.get("tools", []))

        # Plus fallback: web toolset
        web_ts = _toolsets.get("web", {})
        matched_tools.update(web_ts.get("tools", []))

    # Convert to OpenAI tool format
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
    """Get full toolset definitions allowed for a platform (no keyword narrowing)."""
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


# ── Auto-scan tool files ──────────────────────────────────


def auto_scan(path: str = "tools"):
    """Auto-scan all .py files under tools/ directory and import them (triggers deferred @tool registration)."""
    import importlib
    import os

    tools_dir = path
    if not os.path.isdir(tools_dir):
        logger.warning("Tool directory not found: %s", tools_dir)
        return

    # Scan all Python files in the directory
    for fname in sorted(os.listdir(tools_dir)):
        if fname.endswith(".py") and not fname.startswith("_"):
            mod_name = fname[:-3]
            spec = importlib.util.spec_from_file_location(mod_name, os.path.join(tools_dir, fname))
            if spec and spec.loader:
                try:
                    spec.loader.exec_module(importlib.util.module_from_spec(spec))
                    logger.debug("Loaded tool file: %s", fname)
                except Exception as e:
                    logger.warning("Failed to load tool file %s: %s", fname, e)


# ── Tool definition queries ───────────────────────────────


def available_tools() -> list[str]:
    return list(_tool_registry.keys())


def get_tool_metadata(name: str) -> dict | None:
    return _tool_metadata.get(name)
