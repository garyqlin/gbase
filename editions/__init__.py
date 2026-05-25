"""
Gbase edition definitions and module switches.

Each edition is an EditionConfig object defining:
- which modules are enabled
- default port
- default identity
- resource requirements
"""

from dataclasses import dataclass, field
from typing import Dict, Set


@dataclass
class EditionConfig:
    """Gbase edition configuration"""
    name: str                          # edition name: hacker / prime / standard / lite
    label: str                         # label
    port: int                          # default port
    identity: str                      # default identity
    modules: Set[str] = field(default_factory=set)

    @property
    def enabled_modules(self) -> Set[str]:
        return self.modules


# --- Module name constants ---

MOD_SAFETY_GATEWAY  = "safety_gateway"     # safety gateway (six-layer detection chain)
MOD_SANDBOX         = "sandbox_safety"     # sandbox simulation firewall
MOD_MIRROR          = "mirror"             # mirror engine
MOD_EXPERIENCE      = "experience"         # experience engine
MOD_TOOLKIT         = "toolkit"            # tool registration and routing
MOD_LLM             = "llm"                # LLM inference
MOD_AGENT_BASIC     = "agent_basic"        # basic agent scheduler
MOD_DAG_ENGINE      = "dag_engine"         # DAG agent engine
MOD_COGNIFOLD       = "cognifold"          # cognitive fold
MOD_SEARCH_PREEXEC  = "search_preexec"     # search pre-execution
MOD_VILLAGE_OS      = "village_os"         # Village OS world interconnect
MOD_RSI             = "rsi"                # RSI self-evolution
MOD_IDENTITY_PLUG   = "identity_plug"      # pluggable identity
MOD_PROJECT_MEMORY  = "project_memory"     # long project memory
MOD_SCHEDULER       = "scheduler"          # cron scheduler
MOD_EVOLUTION       = "evolution"          # evolution engine
MOD_PORTAL          = "portal"             # Portal admin panel
MOD_LIFELINE        = "lifeline"           # self-rescue system
MOD_KNOWLEDGE_PACK  = "knowledge_pack"     # preloaded domain knowledge pack


# --- Edition definitions ---

# Core modules (all editions)
CORE = {MOD_SAFETY_GATEWAY, MOD_SANDBOX, MOD_MIRROR, MOD_EXPERIENCE, MOD_TOOLKIT, MOD_LLM, MOD_AGENT_BASIC, MOD_SCHEDULER, MOD_LIFELINE}


HACKER = EditionConfig(
    name="hacker",
    label="Hacker",
    port=8420,
    identity="standard",
    modules=CORE | {
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
    label="Prime",
    port=8425,
    identity="prime",
    modules=CORE | {
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
    label="Standard",
    port=8426,
    identity="standard",
    modules=CORE | {
        MOD_DAG_ENGINE,
        MOD_SEARCH_PREEXEC,
        MOD_IDENTITY_PLUG,
        MOD_KNOWLEDGE_PACK,
    },
)

LITE = EditionConfig(
    name="lite",
    label="Lite",
    port=8427,
    identity="lite",
    modules=CORE | {
        MOD_KNOWLEDGE_PACK,
    },
)


# --- Lookup table ---

EDITIONS: Dict[str, EditionConfig] = {
    "hacker": HACKER,
    "prime": PRIME,
    "standard": STANDARD,
    "lite": LITE,
}


def get_edition(name: str) -> EditionConfig:
    """Get edition config by name."""
    cfg = EDITIONS.get(name)
    if not cfg:
        raise ValueError(f"Unknown edition: {name}, available: {list(EDITIONS.keys())}")
    return cfg


def list_editions() -> list:
    """List all editions."""
    return [(c.name, c.label, c.port) for c in EDITIONS.values()]
