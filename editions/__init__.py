"""
Gbase 版本定义与模块开关。

每个版本是一个 EditionConfig 对象，定义：
- 启用哪些模块
- 默认端口
- 默认身份
- 资源需求
"""

from dataclasses import dataclass, field
from typing import Dict, Set


@dataclass
class EditionConfig:
    """Gbase 版本配置"""

    name: str  # 版本名: hacker / prime / standard / lite
    label: str  # 中文标签
    port: int  # 默认端口
    identity: str  # 默认身份
    modules: set[str] = field(default_factory=set)

    @property
    def enabled_modules(self) -> set[str]:
        return self.modules


# ── 模块名称常量 ──

MOD_SAFETY_GATEWAY = "safety_gateway"  # 安全网关（六层检测链）
MOD_SANDBOX = "sandbox_safety"  # 沙箱推演防火墙
MOD_MIRROR = "mirror"  # 鉴面引擎
MOD_EXPERIENCE = "experience"  # 经验引擎
MOD_TOOLKIT = "toolkit"  # 工具注册与路由
MOD_LLM = "llm"  # LLM 推理
MOD_AGENT_BASIC = "agent_basic"  # 基础 Agent 调度
MOD_DAG_ENGINE = "dag_engine"  # DAG Agent 引擎
MOD_COGNIFOLD = "cognifold"  # 认知折叠
MOD_SEARCH_PREEXEC = "search_preexec"  # 搜索预执行
MOD_VILLAGE_OS = "village_os"  # Village OS 世界互联
MOD_RSI = "rsi"  # RSI 自进化
MOD_IDENTITY_PLUG = "identity_plug"  # 身份可插拔
MOD_PROJECT_MEMORY = "project_memory"  # 长项目记忆
MOD_SCHEDULER = "scheduler"  # 定时调度器
MOD_EVOLUTION = "evolution"  # 进化引擎
MOD_PORTAL = "portal"  # Portal 管理面板
MOD_LIFELINE = "lifeline"  # 自救系统
MOD_KNOWLEDGE_PACK = "knowledge_pack"  # 预装行业知识包


# ── 各版本定义 ──

# 核心组件（所有版本都有）
CORE = {
    MOD_SAFETY_GATEWAY,
    MOD_SANDBOX,
    MOD_MIRROR,
    MOD_EXPERIENCE,
    MOD_TOOLKIT,
    MOD_LLM,
    MOD_AGENT_BASIC,
    MOD_SCHEDULER,
    MOD_LIFELINE,
}


HACKER = EditionConfig(
    name="hacker",
    label="极客版",
    port=8420,
    identity="standard",
    modules=CORE
    | {
        MOD_DAG_ENGINE,
        MOD_COGNIFOLD,
        MOD_SEARCH_PREEXEC,
        MOD_VILLAGE_OS,
        MOD_RSI,
        MOD_IDENTITY_PLUG,
        MOD_PROJECT_MEMORY,
        MOD_EVOLUTION,
        MOD_PORTAL,
    },
)

PRIME = EditionConfig(
    name="prime",
    label="旗舰版",
    port=8425,
    identity="prime",
    modules=CORE
    | {
        MOD_DAG_ENGINE,
        MOD_SEARCH_PREEXEC,
        MOD_IDENTITY_PLUG,
        MOD_PROJECT_MEMORY,
        MOD_PORTAL,
        MOD_KNOWLEDGE_PACK,
    },
)

STANDARD = EditionConfig(
    name="standard",
    label="标准版",
    port=8426,
    identity="standard",
    modules=CORE
    | {
        MOD_DAG_ENGINE,
        MOD_SEARCH_PREEXEC,
        MOD_IDENTITY_PLUG,
        MOD_KNOWLEDGE_PACK,
    },
)

LITE = EditionConfig(
    name="lite",
    label="嵌入版",
    port=8427,
    identity="lite",
    modules=CORE
    | {
        MOD_KNOWLEDGE_PACK,
    },
)

POSEIDON = EditionConfig(
    name="poseidon",
    label="波塞冬版",
    port=8428,
    identity="poseidon",
    modules=CORE
    | {
        MOD_DAG_ENGINE,
        MOD_SEARCH_PREEXEC,
        MOD_IDENTITY_PLUG,
        MOD_KNOWLEDGE_PACK,
    },
)


# ── 查找表 ──

EDITIONS: dict[str, EditionConfig] = {
    "hacker": HACKER,
    "prime": PRIME,
    "standard": STANDARD,
    "lite": LITE,
    "poseidon": POSEIDON,
}


def get_edition(name: str) -> EditionConfig:
    """按名称获取版本配置。"""
    cfg = EDITIONS.get(name)
    if not cfg:
        raise ValueError(f"未知版本: {name}, 可选: {list(EDITIONS.keys())}")
    return cfg


def list_editions() -> list:
    """列出所有版本。"""
    return [(c.name, c.label, c.port) for c in EDITIONS.values()]
