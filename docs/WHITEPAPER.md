# GBase — Universal AI Agent Framework

**Version:** 0.4.0 | **License:** MIT | **Repository:** [github.com/garyqlin/gbase](https://github.com/garyqlin/gbase)

> **Abstract.** GBase is a production-grade Python framework for building autonomous AI agents. It provides a complete memory architecture (5-layer hot-to-cold hierarchy), identity-separated personalities, tool auto-registration and routing, multi-agent orchestration via DAG workflows, self-evolution through RSI (Recursive Self-Improvement), anti-fragile stability mechanisms, Feishu/Lark Bot integration, and territory-based security isolation. Designed to be the universal operating system for AI agents — one framework, many identities, persistent memory, and autonomous growth.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Core System: The Kernel](#2-core-system-the-kernel)
3. [Memory System: 5-Layer Hot-to-Cold Hierarchy](#3-memory-system-5-layer-hot-to-cold-hierarchy)
4. [ArchiveStore: Full-Text Storage with Intelligent Retrieval](#4-archivestore-full-text-storage-with-intelligent-retrieval)
5. [Mirror Engine: Short-Term & Working Memory](#5-mirror-engine-short-term--working-memory)
6. [Experience Engine: Learned Patterns](#6-experience-engine-learned-patterns)
7. [Cognifold: Autonomous Knowledge Structuring](#7-cognifold-autonomous-knowledge-structuring)
8. [Agent Identity System](#8-agent-identity-system)
9. [Tool System: Auto-Registration & Routing](#9-tool-system-auto-registration--routing)
10. [Multi-Agent Orchestration: DAG Engine](#10-multi-agent-orchestration-dag-engine)
11. [Self-Evolution (RSI) Framework](#11-self-evolution-rsi-framework)
12. [Anti-Fragile & Stability](#12-anti-fragile--stability)
13. [Territory Safety & Sandbox](#13-territory-safety--sandbox)
14. [Feishu/Lark Bot Integration](#14-feishulark-bot-integration)
15. [Skill System: Plug-and-Play Abilities](#15-skill-system-plug-and-play-abilities)
16. [Pipeline & Distillation](#16-pipeline--distillation)
17. [Scheduler & Autonomous Learning](#17-scheduler--autonomous-learning)
18. [Memory Evaluation & Benchmarking](#18-memory-evaluation--benchmarking)
19. [Deployment & Launchd Management](#19-deployment--launchd-management)
20. [Performance & Scalability](#20-performance--scalability)
21. [Future Roadmap](#21-future-roadmap)
22. [Appendix: Module Inventory](#22-appendix-module-inventory)

---

## 1. Architecture Overview

GBase is structured around **five fundamental design principles**:

1. **Memory First** — An agent is only as good as what it remembers. Memory is not a cache; it is the core of identity and continuity.
2. **Identity Separation** — The same codebase runs multiple agents (Zaku, Hammer, Ink, Bumblebee, Gundam, Poseidon) with distinct personalities, rules, and memories.
3. **Tool Autonomy** — Tools should do real work, not just route to an LLM. Each tool is a first-class capability with its own execution context.
4. **Anti-Fragile by Default** — The system gets stronger from failures. Every error is logged, analyzed, and optionally evolved into a better behavior.
5. **Zero External Dependencies for Core** — No vector DB, no Redis, no message queue. SQLite + JSONL + file system are sufficient for production workloads up to hundreds of thousands of sessions.

### 1.1 Layer Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     Feishu / HTTP / CLI                      │
├─────────────────────────────────────────────────────────────┤
│                        Kernel (run loop)                     │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┐   │
│  │Memory    │ Identity │ Tools    │Skills    │Safety    │   │
│  │(5-layer) │(hotswap) │(60+ auto)│(500+ idx)│(Territory)│   │
│  └──────────┴──────────┴──────────┴──────────┴──────────┘   │
├─────────────────────────────────────────────────────────────┤
│                    Session Manager (JSONL)                    │
├─────────────────────────────────────────────────────────────┤
│              Storage Engine (SQLite + File System)           │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Component Map

| Module | Path | Lines | Role |
|--------|------|-------|------|
| **kernel.py** | `lib/kernel.py` | 77,651 | Core agent loop, system prompt assembly, memory orchestration |
| **main.py** | `main.py` | 16,012 | Entry point: FastAPI server, identity loading, channel init |
| **session.py** | `lib/session.py` | 14,147 | JSONL session management with compression markers |
| **archive_store.py** | `lib/archive_store.py` | 34,210 | Full-text conversation archive with intelligent retrieval |
| **mirror.py** | `lib/mirror.py` | 58,955 | Short-term memory: Ebbinghaus decay, entity graph, recall |
| **experience.py** | `lib/experience.py` | 24,915 | Long-term learned patterns, anti-fragile distillation |
| **cognifold.py** | `lib/cognifold.py` | 22,994 | Autonomous knowledge structure emergence |
| **identity.py** | `lib/identity.py` | 4,442 | Personality, rules, and context for each agent |
| **toolkit.py** | `lib/toolkit.py` | 15,626 | Tool auto-registration, platform routing, schema management |
| **dag_engine.py** | `lib/dag_engine.py` | 27,337 | DAG-based multi-agent workflow orchestration |
| **evolution_engine.py** | `lib/evolution_engine.py` | 14,816 | Self-evolution with auto-rollback |
| **daily_memory.py** | `lib/daily_memory.py` | 12,622 | Cross-session memory injection, daily consolidation |
| **skill_router.py** | `lib/skill_router.py` | 12,612 | Skill index, trigger matching, capability-based routing |
| **sandbox_safety.py** | `lib/sandbox_safety.py` | 8,430 | Sandbox validation, critical file protection |
| **territory.py** | `lib/territory.py` | 1,529 | Cross-agent territory access control |
| **sleep_cycle.py** | `lib/sleep_cycle.py` | 10,076 | Offline maintenance: compression, mirror pruning, gradient |
| **feishu.py** | `lib/channels/feishu.py` | ~8,000 | Full Feishu Bot integration |
| **distill.py** | `tools/distill.py` | ~22,000 | Model distillation pipeline (Ollama/MLX) |
| **self_edit.py** | `tools/self_edit.py` | 14,589 | Safe self-modification with rollback |
| **hive_mind.py** | `tools/hive_mind.py` | 68,820 | Autonomous multi-engine deep research |

---

## 2. Core System: The Kernel

`lib/kernel.py` is the heartbeat of every GBase agent. At 77,651 bytes, it implements the full agent loop:

### 2.1 The `Kernel` Class

```python
class Kernel:
    def __init__(self, identity, llm_client, storage, mirror, ...)
    async def run(user_message, session, ...) -> dict
```

**`run()` lifecycle:**

1. **ArchiveStore Search** — Cross-session full-text retrieval before any processing
2. **System Prompt Assembly** — Dynamic construction from identity, rules, tools, skills, memory, knowledge
3. **RSI Dual-Knob Classification** — Task intent detection sets memory injection volume and LLM temperature
4. **Skill Matching** — Triggers from 500+ indexed skills matched against user message
5. **Memory Warm-Up** — Cross-session pre-warming: injects relevant persistent notes, recent mirror lessons, and handoff context
6. **LLM Call** — With timeout protection, provider fallback chain, and retry
7. **ArchiveStore Append** — Every turn archived as full text
8. **Memory Sedimentation** — Auto-sedimentation of search results into Mirror
9. **Daily Memory Extraction** — Periodic extraction of conversation essence
10. **Trace Finalization** — Log, metrics, note trigger

### 2.2 System Prompt Architecture

The system prompt is constructed in layers (all injected at runtime, not hardcoded):

```
┌─────────────────────────────────────────────────┐
│ 1. Identity Declaration (name, role, rules)      │
│ 2. Tool List (compact, categorized, no schema)   │
│ 3. Skill Router matches (trigger-based)          │
│ 4. RSI Temperature + Task Mode                   │
│ 5. Mirror Hot/Warm Injection (Ebbinghaus-ranked) │
│ 6. Knowledge Auto-Retrieval (keyword → L2)       │
│ 7. Handoff Context (previous session essence)    │
│ 8. Memory Warm-Up (cross-session pre-warm)       │
│ 9. ArchiveStore Search Results (top-5 relevance) │
│ 10. Time & Timezone                              │
│ 11. Cache Boundary: HEARTBEAT.md, dynamic parts  │
│ 12. RSI Dual-Knob Mode (temperature hint)       │
│ 13. Search Budget Guidance                       │
└─────────────────────────────────────────────────┘
```

### 2.3 Provider Chain with Exponential Backoff

```python
PROVIDER_ORDER = ["deepseek"]
```

When the primary provider fails, GBase falls through the chain with exponential backoff. Each failure is logged and the offending provider is removed from the candidate list for that turn. The fallback is transparent to the agent: it sees the response, not the retries.

---

## 3. Memory System: 5-Layer Hot-to-Cold Hierarchy

GBase implements a **five-layer memory hierarchy**, inspired by human memory models and validated against academic benchmarks:

### 3.1 The Five Layers

```
Layer   Name              Persistence     Size      Retrieval
──────  ──────────────    ───────────     ──────    ─────────
L0      Hot Context       1 turn          12-16K    In-prompt (session)
L1      Working Cache     ~30 min         LRU/64    Hot query cache
L2      Mirror (Short)    Days-weeks      ~60K      Ebbinghaus + entity
L3      Experience        Permanent       ~2K       Structured patterns
L4      Notes (L4)        Forever         ~100      Full-text search
L5      ArchiveStore      Forever         ~100K+    Full-text + markers
```

#### L0: Hot Context
The current conversation window. Managed by `JsonlSessionManager` with adaptive compression markers. No semantic compression — full original text preserved.

#### L1: Hot Query Cache (ArchiveStore)
LRU cache of 64 recent entity queries with 1-hour TTL. Accelerates high-frequency entity lookups without Database queries.

#### L2: Mirror Engine (Short-Term Memory)
Persistence: days to weeks, decaying via Ebbinghaus curve.
- **Triple-layer scoring**: intent matching (0.5) × trigger feedback (0.25) × density pruning (0.25)
- **Modified Ebbinghaus curve**: 1h → 1d → 7d → 30d → decay
- **Negative memory noise penalty**: blocks low-information patterns
- **Entity graph**: `relate_entities()` builds a knowledge graph from memory co-occurrence
- **Fuzzy recall**: jieba segmentation + n-gram sliding window for Chinese

#### L3: Experience Engine (Permanent Patterns)
Structured, de-duplicated knowledge learned from past interactions.
- Anti-fragile dual-path: successes AND failures both trigger extraction
- Deduplication via tag-based similarity check
- Version-aware import/export

#### L4: Notes (Permanent, Non-Decaying)
Explicit write-once storage. Created by `note_tool.py` — tool calls or auto-triggered on deep work. Never decays, never auto-deletes. The "when in doubt, it's here" layer.

#### L5: ArchiveStore (Full-Text Archive)
See Section 4 for the full treatment.

### 3.2 Cross-Session Memory Injection

`daily_memory.py` implements a **cross-session memory pre-warming** mechanism:

1. On kernel initialization (for a new session), scan the last 4 compaction markers
2. Extract conversation essence: topics, facts, decisions, pending items
3. Inject as a structured "Earlier in this conversation:" block into the system prompt
4. Track which summaries have already been injected to avoid duplication

This solves the "AI forgets last conversation" problem without requiring any external vector database.

---

## 4. ArchiveStore: Full-Text Storage with Intelligent Retrieval

`lib/archive_store.py` (34,210 bytes) is GBase's flagship memory innovation — replacing all LLM-based compression with full-text archival + intelligent retrieval.

### 4.1 Architecture

```
User/Assistant turns
        │
        ▼
Append (batch: 5 turns) ──→ SQLite (full text)
        │                        │
        ├── Build Marker         │
        │   (event·entity1·...)  │
        ├── Conflict Detection   │
        │   (Cosmos-3 inspired)  │
        └── Hot Cache Update     │
                                 ▼
                     Search (LIKE + BM25 scoring)
                        │
                    ▼────┴────▼
            Timeline Index    Top-5 Results
            (time-based)      (into system prompt)
```

### 4.2 Key Features

#### 4.2.1 M3-Inspired Time Decay Segmentation

```python
# Within 7 days (hot zone): Full weight, no decay
# 7-30 days (warm zone): Linear decay from 1.0 to 0.5  
# 30+ days (cold zone): Exponential decay, ×0.5 every 30 days
```

Inspired by the MiniMax M3 sparse attention architecture — the same principle that makes M3 efficient at long-context is applied here: **not all history is equally relevant. Recent history gets full weight; older history gets progressively discounted but not forgotten.**

#### 4.2.2 Cosmos-3 Inspired Entity Conflict Detection

On each user message, `_check_conflict()` extracts the subject entity and compares it against the last N entries for the same entity. If a conflicting statement is found:

1. A `[⚠️ 与前文记录矛盾]` entry is appended to the archive
2. The conflict is logged for the agent's awareness
3. The original and conflicting statements are both preserved (the agent decides which is correct)

This moves from "forget and overwrite" to "notice and decide" — a fundamental upgrade in agent memory quality.

#### 4.2.3 Timeline Markers

Every archived entry receives a structured marker:

```
event_type·entity1·entity2·entity3
```

Example markers:

| Conversation | Marker |
|-------------|--------|
| "帮我查一下今天的天气" | `查询·天气` |
| "小馒头喜欢吃草莓" | `讨论·小馒头·草莓` |
| "部署到阿里云 ECS 8.153.91.115" | `部署·阿里云·ECS·服务器` |

This enables human-like "first locate, then recall" retrieval — ask the timeline index for recent markers, then drill into the one that looks relevant.

#### 4.2.4 Hot Query Cache (LRU, 64 entries)

```python
self._hot_cache: OrderedDict = OrderedDict()
self._hot_cache_max = 64
self._hot_cache_ttl = 3600  # 1 hour
```

Frequently accessed entity queries stay in memory, avoiding SQLite reads for the same question within the hour. Each cache hit resets the TTL.

#### 4.2.5 Trash Archive

Before deleting old entries (at `MAX_ENTRIES_PER_SESSION=50000`), the oldest 30% are written to `data/archive_trash/{session_key}.jsonl` as plain-text JSONL — grep-able and recoverable without the archive store.

### 4.3 Why Not Vector DB or FTS5

| Option | Decision | Reason |
|--------|----------|--------|
| **Vector DB** | ❌ | Unnecessary complexity for this scale. GBase targets 100K–1M entries per session. |
| **SQLite FTS5** | ❌ | FTS5 unicode61 tokenizer does not index CJK characters. Custom tokenizer requires C extension. |
| **LIKE + BM25** | ✅ | 10K entries: <1ms. 100K entries: <2ms. Zero dependencies. |

---

## 5. Mirror Engine: Short-Term & Working Memory

`lib/mirror.py` (58,955 bytes) is GBase's **Ebbinghaus-based short-term memory** with entity graph capabilities.

### 5.1 Core Mechanism

The Mirror stores memory entries with:
- **Type**: Lesson, Confirmation, Observation, Relationship
- **Importance**: 1–7 scale (RSI Dual-Knob adjustable)
- **Injection hits**: Tracked for feedback scoring
- **Ebbinghaus decay**: Exponential decay curve resets on injection

### 5.2 Triple-Layer Score

```python
_total = intent_match(0.5) + feedback_score(0.25) + density_bonus(0.25)
```

- **Intent matching** (0.5): How well does this memory's tags match the current user input?
- **Trigger feedback** (0.25): Did past injections of this memory lead to tool usage?
- **Density bonus** (0.25): Is this memory information-dense vs. noise?

### 5.3 Noise Filtering

The `_is_noise_memory` filter blocks:
- Single-character memories
- Pure punctuation memories  
- Low-entropy repeating patterns
- Memories matching the noise stopword list

### 5.4 Entity Relationship Graph

```python
mirror.relate_entities("GBase", "ArchiveStore")
mirror.query_relations("GBase", max_depth=2)
# → [{"relation": "component", "entity": "ArchiveStore"}, ...]
```

Built from co-occurrence in mirror entries. Enables associative recall: "what else was I talking about when we discussed memory systems?"

### 5.5 Retrieval: Token-Aware with Ebbinghaus

```python
def recall(query: str, max_tokens: int = 2000) -> list[dict]:
```

- Keyword matching via jieba segmentation + 2-gram sliding window
- Ebbinghaus boost: entries reset by recent injection rank higher
- Token budget: respects `max_tokens` limit, prioritizes high-score entries
- Hot/warm tier separation: hot (~800 chars, always injected) vs. warm (scored injection)

---

## 6. Experience Engine: Learned Patterns

`lib/experience.py` (24,915 bytes) implements **persistent, structured learning** from successes and failures.

### 6.1 Extraction Pipeline

```
Conversation Turn
    │
    ├── Check triggers (deep work, tool calls ≥5, long response)
    │
    ├── Anti-Fragile: Extract failure pattern
    │   │  When tool X failed due to Y, what should I do next time?
    │   │
    └── Anti-Fragile: Extract success pattern
        │  When tool X succeeded with parameter Y, why?
        │
        └── LLM Extraction (with context)
            │
            └── Write to experience.jsonl
                │
                └── Tag-based dedup (skip if similar tags exist)
```

### 6.2 Experience Structure

```json
{
    "id": "exp_abc123",
    "type": "lesson",
    "tags": ["deployment", "cloud", "rustdesk"],
    "content": "RustDesk connects to OPPRIME Windows PC...",
    "importance": 5,
    "inject_hits": 12,
    "source_session": "session_xyz",
    "created_at": "2026-06-02T10:30:00Z"
}
```

### 6.3 Anti-Fragile Dual Path

**Failures** are captured just as eagerly as successes. Every tool error, every wrong parameter, every timeout — the engine asks "what pattern does this represent?", converts it to an experience entry, and tags it for future retrieval.

This ensures the agent learns from mistakes without needing explicit negative feedback.

---

## 7. Cognifold: Autonomous Knowledge Structuring

`lib/cognifold.py` (22,994 bytes) implements a **three-layer emergent knowledge structuring** system, inspired by conceptual spaces theory.

### 7.1 Three Layers

| Layer | Function | Implementation |
|-------|----------|----------------|
| **L1: Event Ingestion** | Receive raw mirror entries, tool calls, user messages | Tokenized → normalized → stored as event nodes |
| **L2: Concept Clustering** | Self-organize events into concept clusters | TF-IDF + cosine similarity, auto-merge threshold |
| **L3: Intent Emergence** | Detect recurring intents from cluster patterns | Markov chain on cluster transitions |

### 7.2 Integration Points

Cognifold is designed as a second-pass knowledge layer:
- **Input**: Mirror entries (already scored and filtered)
- **Processing**: Background (non-blocking, spawned as asyncio task)
- **Output**: Concept clusters that feed into system prompt as "topical knowledge"

Note: As of v0.4.0, Cognifold is structurally complete but full integration into the kernel loop is pending production validation.

---

## 8. Agent Identity System

`lib/identity.py` (4,442 bytes) implements **hot-swappable agent personalities**.

### 8.1 Identity Structure

```
identities/
├── default/       # Fallback identity
├── hammer/        # Code engineer
├── ink/           # UI/design specialist
├── bumblebee/     # Research swarm leader
├── laser/         # White-box tester
├── forge/         # Black-box tester
├── gundam/        # Full-feature Feishu bot
└── poseidon/      # Advanced Feishu bot (experimental)
```

Each identity directory contains:
- `system_prompt.md` — Personality and rules
- `rules/*.md` — Custom rule files
- `exclusions.txt` — Excluded tools/skills (optional)

### 8.2 Identity Loading in main.py

```python
identity = Identity(IDENTITY_DIR)
kernel = Kernel(
    identity=identity,
    llm_client=LLMClient(...),
    storage=Storage(...),
    mirror=Mirror(...),
    ...
)
```

### 8.3 Identity-Aware Tool Routing

```python
# In toolkit.py
register_toolset("deploy", [
    "deploy", "部署", "发布",
], platforms=["hammer", "gundam"])
register_toolset("research", [
    "research", "搜索", "调研", "survey",
], platforms=["bumblebee"])
```

---

## 9. Tool System: Auto-Registration & Routing

`lib/toolkit.py` (15,626 bytes) + `tools/__init__.py` define GBase's **60+ auto-registered tools**.

### 9.1 Auto-Registration via Decorator

```python
# In tools/write_file.py
@tool()
def write_file(filepath: str, content: str, mode: str = "w") -> dict:
    """Write content to a file."""
    ...
```

The `@tool()` decorator from `lib/toolkit.py`:
1. Introspects the function signature
2. Extracts the docstring as description
3. Generates an OpenAI-compatible tool schema
4. Registers it in the global tool registry
5. Maps it to toolset categories

### 9.2 Tool Categories

| Category | Tools | Example |
|----------|-------|---------|
| **File** | read_file, write_file, exec, my_path | File system operations |
| **Code** | commit_helper, crypto_helper, jwt_helper | Dev utilities |
| **Communication** | feishu_send, feishu_send_file, feishu_card, mail | Cross-channel messaging |
| **Research** | honeycomb_search, anysearch_tool, hive_mind | Multi-engine research |
| **Memory** | note_tool, learn, mirror_tool | Persistent memory |
| **Document** | pdf_gen, docx_gen, xlsx_gen, pptx_gen | Document generation |
| **Testing** | test_generator, laser_doc, forge_verify | Quality assurance |
| **Self-Modify** | self_edit, anchor_keeper | Safe self-modification |
| **Network** | network_tools, mock_server | Network utilities |
| **Image** | yf_image_tools | Image generation (SiliconFlow) |
| **Security** | security_watch, log_profiler | Security monitoring |
| **Orchestration** | trident_tools, schema_tools | Multi-agent tools |

### 9.3 Tool Routing

```python
# Platform-based routing: which identity sees which toolset
register_platform_map({
    "hammer":    ["code", "file", "deploy"],
    "ink":       ["ui", "image", "document"],
    "bumblebee": ["research", "search", "data"],
    "laser":     ["testing", "code", "document"],
    "forge":     ["testing", "security", "code"],
    "default":   ["*"],  # Falls through to all tools
})
```

---

## 10. Multi-Agent Orchestration: DAG Engine

`lib/dag_engine.py` (27,337 bytes) + `lib/dag_agents.py` (21,646 bytes) implement **DAG-based multi-agent workflow orchestration**.

### 10.1 Core Concepts

```python
# Define a workflow step
Step = {
    "id": "step-1",
    "agent": "hammer",       # Which identity runs this step
    "task": "Write tests for auth module",
    "depends_on": ["step-0"],  # DAG dependency
    "inputs": {"module": "auth"},
    "timeout": 300,
    "retry": 2,
}

# A workflow is a DAG of steps
Workflow = {
    "id": "wf-auth-test",
    "name": "Auth Module Testing",
    "steps": [Step, ...],
    "on_failure": "rollback",
}
```

### 10.2 DAG Engine Capabilities

- **Topological execution**: Steps run in dependency order, in parallel when possible
- **Three-layer memory**: Each step gets its own hot/working/archive context
- **Safety hooks**: `disk_safety_hook`, `mem_safety_hook` run before each step
- **Pilot workflows**: Pre-built templates for common patterns (audit, test, deploy)
- **Status tracking**: Enum-based status machine (PENDING → RUNNING → SUCCESS/FAILED)

### 10.3 Agent Functions (dag_agents.py)

Pre-built agent functions for common DAG tasks:

| Function | Purpose |
|----------|---------|
| `agent_whitebox_check` | White-box code review |
| `agent_blackbox_check` | Black-box functional testing |
| `agent_swarm_test` | Multi-agent parallel testing |
| `agent_health_check` | System health check |
| `agent_arch_audit` | Architecture compliance audit |
| `agent_weekly_report` | Aggregated weekly summary |

---

## 11. Self-Evolution (RSI) Framework

`lib/evolution_engine.py` (14,816 bytes) implements **Recursive Self-Improvement** — the agent evaluates, learns from, and modifies its own behavior.

### 11.1 Phase Architecture

```
Phase 1: Trigger Rule Engine
    └── Detect events: tool errors, user feedback, performance regression

Phase 2: Multi-Angle Evaluation
    ├── Speed angle: Did the tool call take too long?
    ├── Quality angle: Did it produce correct output?
    └── Relevance angle: Was it the right tool for the task?

Phase 3: Auto-Rollback Decision
    ├── If change degraded performance → rollback
    └── If change improved performance → keep + log

Phase 4: Post-Recovery Diagnosis
    └── Write diagnosis to experience.jsonl
```

### 11.2 RSI Dual-Knob: Task-Adaptive Memory

```python
_TEMP_CONFIG = {
    "explore":  {"mode": "warm", "mirror_max": 4,  "experience_max": 2},
    "execute":  {"mode": "cold", "mirror_max": 6,  "experience_max": 3},
    "discuss":  {"mode": "warm", "mirror_max": 3,  "experience_max": 1},
    "maintain": {"mode": "cold", "mirror_max": 5,  "experience_max": 2},
}
```

On each user message, the RSI classifier detects the task type:
- **explore**: Research/analysis → warm mode, low memory injection (broad thinking)
- **execute**: Code/deploy → cold mode, high memory injection (focused precision)
- **maintain**: Debug/fix → cold mode, moderate injection
- **discuss**: Chat/feedback → warm mode, minimal injection

### 11.3 Self-Modification Tools

`tools/self_edit.py` (14,589 bytes) provides **safe code self-modification**:

```python
self_edit(path, old_text, new_text)     # Find + replace
verify(path)                              # Syntax check
rollback(path, backup_id)                 # One-command revert
restart()                                 # Restart the agent
```

Every edit:
1. Creates a backup (`.gbase_rollback/`)
2. Verifies Python syntax
3. Only allows `.py` files in designated paths
4. Logs the change reason to experience

---

## 12. Anti-Fragile & Stability

GBase is designed to **get stronger from failures**, not just survive them.

### 12.1 Anti-Fragile Mechanisms

| Mechanism | Module | Description |
|-----------|--------|-------------|
| **Cognizant Redundancy** | kernel.py | Tool fallback paths auto-configured |
| **Three-Tier Sandbox** | sandbox_safety.py | File, content, and failure-pattern checks |
| **Silent Error Recovery** | kernel.py | Provider failover with exponential backoff |
| **Loop Deadlock Detection** | kernel.py | >5 recursive loops → break + explain |
| **Black-Scholes Bandwidth** | mirror.py | Noise-proportional memory decay |
| **Experience Dual-Write** | experience.py | Failures AND successes both trigger extraction |
| **Self-Modification Rollback** | self_edit.py | Every edit backed up, revertable |
| **Offline Consolidation** | sleep_cycle.py | Background gradient summarization |

### 12.2 Framework Introspection

```python
# Anti-fragile: loop counting + framework introspection
self._loop_count = 0
self._fallback_tool_map = {}  # Tool fallback paths
```

Every 50 loops, the kernel runs a framework self-check:
1. Are all critical files accessible?
2. Are mirrors within size limits?
3. Are experience entry counts reasonable?
4. Is the WAL checkpoint healthy?

---

## 13. Territory Safety & Sandbox

`lib/territory.py` (1,529 bytes) implements **cross-agent access control** — a GBase agent can only write to its own home directory.

### 13.1 How It Works

```python
# Each agent sets its home
GBASE_HOME=/Users/gary/gundam-home
GBASE_AGENT_NAME=gundam
GBASE_AGENT_HOMES=poseidon:/Users/gary/poseidon-home:gundam:/Users/gary/gundam-home
```

**Write rule:** If `write_file(path)` targets another agent's home → `PermissionError`
**Read rule:** If `read_file(path)` targets another agent's home → Warning logged, allowed (read is read)

Tools instrumented:
- `tools/write_file.py` — Hard block on cross-territory write
- `tools/read_file.py` — Log warning on cross-territory read
- `tools/exec.py` — Scan command for `cd` + redirect patterns

### 13.2 Sandbox Safety

`lib/sandbox_safety.py` (8,430 bytes) provides pre-write validation:

```python
def is_critical(file_path: str) -> bool:
    # Kernel, main.py, identity files
def has_critical_change(content: str) -> bool:
    # Contains dangerous patterns (exec, eval, os.system)
def check_failure_patterns(file_path, new_content) -> list[str]:
    # Known failure patterns from experience
def run_sandbox(file_path, new_content) -> dict:
    # Execute new code snippet, capture output
```

---

## 14. Feishu/Lark Bot Integration

`lib/channels/feishu.py` (~8,000 bytes) implements a **full-featured Feishu (Lark) bot** with:

### 14.1 Capabilities

- **Message receiving**: Webhook event handler (POST /feishu)
- **Message sending**: send_text, send_card, send_file
- **File handling**: Download files from Feishu, send generated files back
- **Encryption**: AES-256-CBC decryption for encrypted Feishu events
- **Heartbeat**: Periodic health check, auto-reconnect
- **PDF extraction**: Multi-layer (PyMuPDF → OCR → local path)
- **Rate limiting**: Per-user token bucket
- **Timeout handling**: 600s timeout (from 120s after stability issues)

### 14.2 Integration Flow

```
Feishu Event → Webhook POST → 200 (immediate ACK)
                                  │
                                  └── Background task
                                      │
                                      ├── Decrypt (if encrypted)
                                      ├── Download files (if any)
                                      ├── Kernel.run(user_message)
                                      │
                                      └── send_text / send_card
```

### 14.3 Configuration

Loaded from environment variables (never hardcoded in source):

```bash
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_ENCRYPT_KEY=
FEISHU_PORT=8440
```

---

## 15. Skill System: Plug-and-Play Abilities

`lib/skill_router.py` and `lib/skill_loader.py` implement a **trigger-based skill system**. Skills are YAML files defining:

```yaml
name: "weather-query"
description: "回答天气相关查询"
triggers: ["天气", "温度", "weather", "气温"]
model: "deepseek-chat"
```

### 15.1 Skill Indexing

Skills are indexed into a SQLite SKILL-INDEX with fields:
- **Trigger keywords**: Matched against user message
- **Tags**: Category + domain tags for routing
- **Capability**: What the skill can do
- **Agent mapping**: Which identity should handle this

The current index holds **500+ skills** across 8 skill categories.

### 15.2 Skill Router Workflow

1. User message enters `run()`
2. `skill_router.py` matches against all trigger keywords
3. Matched skills are injected into system prompt header
4. Agent can call skill tools when relevant

---

## 16. Pipeline & Distillation

`tools/distill.py` (~22,000 bytes) implements a **model distillation pipeline** for creating smaller, task-specific models from accumulated experience.

### 16.1 Pipeline Stages

1. **Export** (`distill_export`): Extract experiences into training format
2. **Train** (`distill_train`): Fine-tune (Ollama + LoRA / MLX)
3. **Evaluate** (`distill_eval`): Test trained model against experience benchmarks
4. **Push** (`distill_push`): Upload to model registry

### 16.2 Distillation Target

The goal is to distil the full GBase agent (powered by DeepSeek-chat) into a small, local model capable of:
- Pattern-matching experience retrieval
- Routine tool orchestration
- LLM-less fallback for simple queries

---

## 17. Scheduler & Autonomous Learning

`lib/scheduler.py` and `tools/learn.py` implement **cron-based autonomous scheduling** and **self-directed learning**.

### 17.1 Cron-Based Task Scheduling

`lib/scheduler.py` (15,459 bytes) provides a SQLite-backed cron scheduler:

```python
# Schedule a task
task_id = scheduler.add_task(
    schedule="0 14 * * *",  # Cron expression
    name="daily-consolidation",
    callback=lambda: self._consolidate()
)
```

Capabilities:
- **Standard cron expressions**: `*/N`, comma-separated, range
- **SQLite persistence**: Survives restarts
- **Polling loop**: 30-second tick, non-blocking async
- **Sender integration**: Can send Feishu messages on task completion

### 17.2 Autonomous Learning Loop

The AutoLearner periodically:
1. Fetches RSS feeds from configured sources
2. Summarizes articles via LLM
3. Extracts experiences from summaries
4. Writes to experience.jsonl with appropriate tags

### 17.3 Offline Consolidation (Sleep Cycle)

`lib/sleep_cycle.py` (10,076 bytes) runs periodic maintenance:

| Stage | Operation | Frequency |
|-------|-----------|-----------|
| **A** | Session compression markers | Configurable |
| **B** | Mirror noise pruning | Configurable |
| **C** | Gradient summarization to experience | Configurable |
| **D** | Mirror disk size report | Configurable |


---

## 18. Memory Evaluation & Benchmarking

GBase includes a **14-dimension memory evaluation tool** for assessing agent memory quality.

### 18.1 Evaluation Dimensions

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Hot recall | 10% | Ability to remember within-session details |
| Warm recall | 10% | Cross-session recall same day |
| Cold recall | 10% | Cross-session recall after days |
| Long-tail recall | 5% | Rare entity recall |
| Anti-interference | 5% | Not confusing similar memories |
| Factual accuracy | 10% | Correct details vs hallucination |
| Temporal awareness | 10% | Understanding chronology |
| High-density injection | 5% | Handling rapid topic shifts |
| Entity binding | 5% | Correct entity-attribute association |
| Consistency | 5% | Not contradicting itself |
| Cross-session warm-up | 5% | Pre-warming effect on recall |
| Meta-memory | 10% | Knowing what it knows and doesn't |
| Session isolation | 10% | Not leaking data between sessions |
| Conflict detection | 5% | Detecting contradictory statements |

### 18.2 Grade Scale

| Grade | Range | Meaning |
|-------|-------|---------|
| **S** | 90-100 | Production-ready |
| **A** | 80-89 | Good, minor gaps |
| **B** | 60-79 | Functional, known issues |
| **C** | 40-59 | Poor, significant gaps |
| **D** | <40 | Not usable |

### 18.3 Test Results (Gundam v3.1, June 2026)

- **Gundam**: 92.1/100 (S) — Strongest memory across all agents
- **Poseidon**: 80.3/100 (A) — Weaker meta-memory (3/10) and session isolation (0/10)
- Key gap identified: Poseidon lacked full ArchiveStore upgrades and session isolation


---

## 19. Deployment & Launchd Management

GBase agents are deployed as macOS **launchd daemons** for auto-restart and resilience.

### 19.1 Launchd Service Template

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>KeepAlive</key><true/>
    <key>RunAtLoad</key><true/>
    <key>Label</key><string>com.opprime.lancer-cc</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/python3</string>
        <string>/path/to/main.py</string>
    </array>
    <key>StandardErrorPath</key>
    <string>/path/to/logs/service.log</string>
    <key>StandardOutPath</key>
    <string>/path/to/logs/service.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>WorkingDirectory</key>
    <string>/path/to/service</string>
</dict>
</plist>
```

### 19.2 Deployed Agents (14 Ports)

| Agent | Port | Start Script |
|-------|------|-------------|
| Standard (default) | 8420 | `start-standard.sh` |
| Hammer (code) | 8431 | `start-hammer.sh` |
| Ink (UI/design) | 8432 | `start-ink.sh` |
| Portal | 8433 | `start-portal.sh` |
| Bumblebee (research) | 8434 | `start-bumblebee.sh` |
| Laser (white-box) | 8435 | `start-laser.sh` |
| Forge (black-box) | 8436 | `start-forge.sh` |
| Gundam | 8440 | `start-gundam.sh` |
| Lancer CC | 8441 | `start-cc.sh` |
| Lancer X | 8442 | `start-x.sh` |
| Poseidon | 8428 | Managed by Gundam home |
| Opprime (cloud) | 8420 (ECS) | PM2 on Aliyun ECS |
| Herald | TBD | Health reporting |

### 19.3 Monitoring & Alerts

- **Launchd auto-restart**: On crash or port conflict, launches immediately
- **Throttle interval**: 10 seconds between restart attempts (prevents crash loops)
- **Logs**: Timed-rotating files (90-day retention)
- **Heartbeat**: Periodic health check with auto-reconnect
- **WAL checkpoint**: Every 10 minutes + on process exit (prevents SQLite corruption)


---

## 20. Performance & Scalability

### 20.1 Measured Performance

| Operation | Data Size | Latency |
|-----------|-----------|---------|
| ArchiveStore LIKE search | 10K entries | <1ms |
| ArchiveStore LIKE search | 100K entries | <2ms |
| Mirror warm-up | 60K entries, hot | ~800 chars injected |
| Session build_context | 500 entries | <5ms |
| Kernel.prompt assembly | 50+ components | <50ms |
| Feishu message round-trip | N/A | ~200-500ms |

### 20.2 Memory Usage

| Component | Memory |
|-----------|--------|
| Base kernel (idle) | ~120MB |
| With Feishu channel | ~180MB |
| With DAG engine loaded | ~220MB |
| Per-session (active) | ~5-15MB |

### 20.3 Scalability Constraints

| Constraint | Limitation | Mitigation |
|-----------|------------|------------|
| SQLite single-writer | WAL concurrent reads OK | Batch flush (5 turns) |
| JSONL append-only | File grows unbounded | Trim + archive at 50K entries |
| Mirror memory | 60K entries ≈ 300MB | Ebbinghaus decay + pruner |
| Python GIL (tools) | CPU-bound tools block | asyncio for I/O tools |

### 20.4 Zero-Compromise Decisions

GBase deliberately chooses **simplicity over scalability at current scale**:

| Pattern | Not Using | Reason |
|---------|-----------|--------|
| Vector search | ✅ Not needed | LIKE <2ms at 100K entries |
| External DB | ✅ SQLite only | Zero ops overhead |
| Message broker | ✅ In-process FIFO | One machine, no distribution needed |
| Containerization | ✅ Direct deployment | launchd > Docker on macOS |
| ORM / migration | ✅ Raw SQL | Full control, no magic |


---

## 21. Future Roadmap

### Short Term (v0.5.0, Q3 2026)

- **ArchiveStore v3**: Multi-session cross-reference + entity linking
- **Cognifold integration**: Full production loop integration
- **Memory evaluation automation**: CI-gated testing
- **Session isolation fix**: Complete isolation per agent
- **Meta-memory improvement**: "I don't know" awareness + hallucination detection

### Medium Term (v0.6.0, Q4 2026)

- **Sub-agent system**: Parallel sub-agent spawning with results merge
- **MCP integration fully**: Lancer CC/X style external tool bridges
- **Multi-modal memory**: Image recognition results in archive
- **Conflict resolution**: Auto-resolve entity conflicts with evidence
- **Web search integration**: Built-in fallback search for unknown topics

### Long Term (v1.0, 2027)

- **Self-improving compiler**: Distilled model pipeline running continuously
- **Distributed memory**: Cross-instance archive sharing
- **GUI management console**: Visual agent monitoring and memory browser
- **Plugin marketplace**: Third-party skill ecosystem


---

## 22. Appendix: Module Inventory

### 'lib/' — Core Library Modules

| File | Size | Purpose |
|------|------|---------|
| `archive_store.py` | 34KB | Full-text conversation archive + intelligent retrieval |
| `auto_learn.py` | 17KB | Self-directed RSS learning pipeline |
| `backup.py` | 6KB | Automatic backup with rotation |
| `battle_protocol.py` | 5KB | Inter-agent communication protocol |
| `cognifold.py` | 23KB | Autonomous knowledge structure emergence |
| `dag_agents.py` | 22KB | Pre-built agent functions for DAG workflows |
| `dag_engine.py` | 27KB | DAG workflow orchestration engine |
| `dag_orchestrator.py` | 18KB | DAG execution orchestration |
| `daily_memory.py` | 13KB | Cross-session memory injection |
| `evolution_engine.py` | 15KB | RSI self-evaluation + auto-rollback |
| `exec.py` | 3KB | Safe shell command execution |
| `experience.py` | 25KB | Long-term learned patterns + anti-fragile |
| `fetcher.py` | 6KB | URL content fetcher |
| `identity.py` | 4KB | Agent personality + rules |
| `kernel.py` | 78KB | Core agent loop + system prompt assembly |
| `lifeline.py` | 15KB | Self-preservation and safety checks |
| `loop_cache.py` | 6KB | In-memory LRU cache for tool results |
| `mirror.py` | 59KB | Ebbinghaus short-term memory + entity graph |
| `pipeline.py` | 14KB | Sequential task pipeline |
| `project_memory.py` | 13KB | Per-project persistent state |
| `rss_fetcher.py` | 15KB | RSS feed fetching + scheduled polling |
| `safe_shell.py` | 3KB | Shell command wrapper with timeout |
| `sandbox_safety.py` | 8KB | Pre-write validation + critical file check |
| `scheduler.py` | 15KB | Cron-based task scheduling |
| `session.py` | 14KB | JSONL session manager + compression markers |
| `skill_loader.py` | 6KB | Skill file parsing + registration |
| `skill_router.py` | 13KB | Trigger-based skill routing |
| `sleep_cycle.py` | 10KB | Background maintenance + consolidation |
| `storage.py` | 10KB | SQLite storage abstraction |
| `territory.py` | 2KB | Cross-agent territory access control |
| `toolkit.py` | 16KB | Tool auto-registration + platform routing |
| `tracer.py` | 8KB | Tool call logging + metrics |
| `village_connector.py` | 5KB | Inter-agent message relay |
| `channels/feishu.py` | ~8KB | Full Feishu Bot integration |

### 'tools/' — Tools (Auto-Registered)

| File | Size | Category |
|------|------|----------|
| `__init__.py` | 15KB | Tool registration + sets |
| `anchor_keeper.py` | 4KB | Git anchor file management |
| `anysearch_tool.py` | 4KB | Unified search interface |
| `chain.py` | 1KB | Tool chaining |
| `commit_helper.py` | 3KB | Git commit helper |
| `cron.py` | 4KB | Cron task management |
| `crypto_helper.py` | 3KB | Encryption utilities |
| `cua_tools.py` | 7KB | CUA (Computer Use) agent tools |
| `data_seeder.py` | 2KB | Test data generation |
| `distill.py` | 22KB | Model distillation pipeline |
| `docx_gen.py` | 3KB | MS Word generation |
| `exec.py` | 3KB | Shell exec |
| `feishu_card.py` | 3KB | Feishu interactive card builder |
| `feishu_send.py` | 3KB | Feishu text message |
| `feishu_send_file.py` | 5KB | Feishu file upload |
| `file_checker.py` | 3KB | File existence/content check |
| `forge_verify.py` | 11KB | Black-box verification |
| `gen_pro_report.py` | 16KB | Professional report generation |
| `hive_mind.py` | 69KB | Multi-engine deep research |
| `honeycomb_search.py` | 19KB | Multi-site parallel search |
| `jwt_helper.py` | 3KB | JWT token management |
| `knowledge.py` | 16KB | Knowledge base CURD |
| `laser_doc.py` | 5KB | Documentation QA check |
| `learn.py` | 6KB | Self-learning trigger |
| `log_profiler.py` | 2KB | Log analysis |
| `mail.py` | 3KB | Email (OpprimeWorld) |
| `memory_profiler.py` | 2KB | Memory usage profiling |
| `mirror_tool.py` | 0.5KB | Mirror introspection |
| `mock_server.py` | 3KB | HTTP mock server |
| `my_path.py` | 1KB | Path resolution |
| `network_tools.py` | 3KB | Network diagnostics |
| `note_tool.py` | 7KB | L4 permanent notes |
| `pdf_gen.py` | 4KB | PDF generation |
| `pptx_gen.py` | 4KB | PowerPoint generation |
| `prompt_helper.py` | 2KB | Prompt template management |
| `qa_check.py` | 15KB | QA verification |
| `query_profiler.py` | 2KB | Query performance profiling |
| `read_file.py` | 4KB | File reader with format detection |
| `reminder.py` | 2KB | Scheduled reminders |
| `rollback.py` | 2KB | Code rollback |
| `schema_tools.py` | 3KB | Data schema tools |
| `search.py` | 21KB | Web search |
| `search_bridge.py` | 5KB | Search API bridge |
| `search_tunnel.py` | 2KB | Search tunnel proxy |
| `search_webui.py` | 24KB | WebUI for search |
| `security_watch.py` | 3KB | Security monitoring |
| `self_edit.py` | 15KB | Safe self-modification |
| `self_search.py` | 4KB | Internal knowledge search |
| `test_gen.py` | 3KB | Test case generation |
| `test_generator.py` | 4KB | Auto test generator |
| `trident_tools.py` | 9KB | Lancer CC/X coordination |
| `weather.py` | 1KB | Weather query |
| `write_file.py` | 5KB | File writer with safety |
| `xlsx_gen.py` | 3KB | Excel generation |
| `yf_image_tools.py` | 13KB | Image generation (SiliconFlow) |

---

*Generated from GBase v0.4.0 source code. Last updated: June 2, 2026.*
---

## 17. Scheduler & Autonomous Learning

`lib/scheduler.py` (15,459 bytes) + `lib/auto_learn.py` (17,281 bytes) + `lib/daily_memory.py` (12,622 bytes) implement GBase's **autonomous scheduling and learning pipeline**.

### 17.1 Cron Scheduler

`lib/scheduler.py` implements a lightweight, SQLite-backed cron scheduler with zero external dependencies:

```python
scheduler = CronScheduler("data/scheduler.db")
scheduler.add_job(
    name="rss-digest",
    schedule="0 */4 * * *",  # Every 4 hours
    handler=run_rss_digest,
    metadata={"type": "learning"}
)
await scheduler.run()  # Polling loop
```

- **Cron expression parsing**: Standard 5-field cron (min hour dom mon dow)
- **Database persistence**: Jobs survive process restart
- **Failure handling**: Exponential backoff, max 3 retries
- **Async execution**: Jobs run as asyncio tasks
- **Health reporting**: `GET /health` includes scheduler status

### 17.2 Autonomous Learning Pipeline (`auto_learn.py`)

The AutoLearner runs four autonomous learning tasks on a schedule:

| Task | Schedule | Description |
|------|----------|-------------|
| **RSS Digest** | Every 4h | Fetch RSS feeds, LLM-summarize, store as experience |
| **Experience Consolidation** | Daily | Prune duplicate experiences, merge related |
| **Mirror Gradient** | Every 6h | Summarize mirror patterns into experiences |
| **Knowledge Refresh** | Daily | Re-index knowledge entries, fix stale paths |

### 17.3 Offline Consolidation (`sleep_cycle.py`)

`lib/sleep_cycle.py` (10,076 bytes) runs as a background maintenance cycle:

```
Stage A: Session Compression
    └── Remove compression markers for sessions past their TTL

Stage B: Mirror Noise Pruning
    └── Delete mirror entries with zero injection hits over lifetime

Stage C: Gradient Summarization
    └── Distil mirror clusters into experience entries

Stage D: Disk Usage Report
    └── Log mirror DB size, session count, archive size
```

### 17.4 RSS Learning (`lib/rss_fetcher.py` + `lib/fetcher.py`)

GBase can **autonomously learn from RSS feeds**:
1. Fetch RSS articles
2. LLM-summarize into structured knowledge
3. Write as experience entries with `rss` tag
4. Store in SQLite for dedup

---

## 18. Memory Evaluation & Benchmarking

GBase includes a **14-dimension memory evaluation suite** (`tools/qa_check.py`, 14,584 bytes) for systematically testing agent memory quality.

### 18.1 Evaluation Dimensions

| # | Dimension | Description | Weight |
|---|-----------|-------------|--------|
| 1 | Hot Context | Can the agent recall the last 2 turns verbatim? | 10 |
| 2 | Short-Term Mirror | Can it recall a fact from 10 turns ago? | 10 |
| 3 | Experience Retrieval | Can it match a problem to a stored pattern? | 10 |
| 4 | Archive Search | Can it find a fact from a previous session? | 10 |
| 5 | Cross-Session Recall | Can it maintain context across sessions? | 8 |
| 6 | Entity Resolution | Can it correlate entities across time? | 8 |
| 7 | Time-Sensitivity | Does it correctly prioritize recent over stale? | 7 |
| 8 | Conflict Detection | Does it notice contradictory statements? | 7 |
| 9 | High-Density Recall | Can it handle 20+ facts in one query? | 7 |
| 10 | Metacognition | Can it articulate what it does/doesn't know? | 5 |
| 11 | Session Isolation | Does Session A leak into Session B? | 5 |
| 12 | Cross-Session Warm-Up | Does pre-warming improve cold-start recall? | 5 |
| 13 | Memory Persistence | Do facts survive a full restart? | 5 |
| 14 | Self-Correction | Can it correct its own earlier mistake? | 3 |

### 18.2 Scoring Protocol

Each test is a structured scenario with:
- **Setup**: Pre-load memory, archive, or experience
- **Prompt**: Natural-language query
- **Expected**: Exact or semantic answer criteria
- **Score**: 0–10 per test, aggregated to 0–100 overall

### 18.3 Known Benchmarks (v0.4.0 Results)

| Agent | Score | Grade | Key Gap |
|-------|-------|-------|---------|
| **Gundam** (8440) | 92.1/100 | S | Session isolation (6/10) |
| **Poseidon** (8428) | 80.3/100 | A | Metacognition (3/10), Session isolation (0/10) |

---

## 19. Deployment & Launchd Management

GBase agents run as **macOS launchd daemons** for automatic restart on crash.

### 19.1 Launchd Plist Template

```xml
<key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>10</integer>
<key>WorkingDirectory</key><string>/Users/gary/opprime</string>
<key>EnvironmentVariables</key>
<dict>
    <key>PYTHONPATH</key>
    <string>/Users/gary/opprime</string>
</dict>
```

### 19.2 Agent Portfolio (14 Ports)

| Agent | Port | Identity | Role |
|-------|------|----------|------|
| **Gundam** | 8440 | gundam | Full Feishu bot, production |
| **Poseidon** | 8428 | poseidon | Advanced Feishu bot, experimental |
| **Hammer** | 8431 | hammer | Code engineer |
| **Ink** | 8432 | ink | UI/design specialist |
| **Bumblebee** | 8434 | bumblebee | Research swarm leader |
| **Laser** | 8435 | laser | White-box tester |
| **Forge** | 8436 | forge | Black-box tester |
| **Lancer CC** | 8441 | lancer-cc | Full-stack programming agent |
| **Lancer X** | 8442 | lancer-x | Code audit & security |
| *(Standard)* | 8420 | default | Base identity |

### 19.3 Operations

```bash
# Reload a plist after modification
launchctl bootout gui/501 ~/Library/LaunchAgents/com.opprime.gundam.plist
launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.opprime.gundam.plist

# Check status
launchctl list | grep opprime

# Tail logs (all agents share stdout/stderr per plist)
tail -f ~/opprime/logs/launchd-gundam.log
```

---

## 20. Performance & Scalability

### 20.1 Storage Benchmarks (Production v0.4.0)

| Operation | Scale | Time | Notes |
|-----------|-------|------|-------|
| ArchiveStore LIKE query | 10K entries | <1ms | Filtered by session_key + timestamp |
| ArchiveStore LIKE query | 100K entries | <2ms | Same index |
| Mirror recall | 5K entries | <5ms | Ebbinghaus scoring + Top-K sort |
| Mirror entity graph query | 1K entities | <3ms | Depth 2 traversal |
| Session append | Single entry | <0.1ms | JSONL line |
| System prompt assembly | Full pipeline | <50ms | All 13 layers |
| First LLM response | DeepSeek-chat | ~2–8s | Network-dependent |

### 20.2 Memory Footprint

| Component | Memory | Notes |
|-----------|--------|-------|
| Base process | ~150MB | Python 3.13 + uvicorn |
| Mirror DB (10K entries) | ~8MB | SQLite in WAL mode |
| ArchiveStore (100K entries) | ~200MB | Full text + indices |
| Hot cache (64 entries) | ~2MB | LRU in memory |
| **Total per agent** | ~400MB | Steady state |

### 20.3 Scalability Constraints

- **PGLite**: Not multi-process safe — each agent needs its own horizon/braindb
- **Launchd**: ThrottleInterval 10s can cause cascading restarts under port conflicts
- **macOS TCC**: Launchd processes need user-granted Desktop access via GUI popup (macOS 15+)

---

## 21. Future Roadmap

### v0.5.0 (Current Development)

| Feature | Priority | Status |
|---------|----------|--------|
| DeepSeek provider chaining | P0 | ✅ Done |
| Feishu card enhancement | P1 | 🔄 In progress |
| Multi-agent session sharing | P1 | 🔄 In progress |
| Cross-machine sync protocol | P2 | 📋 Planned |

### v0.6.0

| Feature | Description |
|---------|-------------|
| **Multi-model provider chain** | Automatic failover across DeepSeek → SiliconFlow → local |
| **Fediverse/Mastodon channel** | Cross-platform agent deployment |
| **Memory federation** | Share experiences across agents on different machines |
| **Tiered compression** | Optional LLM-compressed summary for extremely long sessions |

### v1.0.0 Release Goals

| Goal | Success Criteria |
|------|------------------|
| Production stability | Mean time between restart: >7 days |
| Memory benchmark >95 | All agents score S-grade or above |
| Public API stability | Backward-compatible API surface for 3 minor versions |
| Multi-platform deployment | macOS + Linux + containerized |

---

## 22. Appendix: Module Inventory

### Core Library (`lib/`)

| Module | Size | Purpose |
|--------|------|---------|
| kernel.py | 77,651 | Core agent loop, system prompt, memory orchestration |
| mirror.py | 58,955 | Short-term memory with Ebbinghaus decay + entity graph |
| archive_store.py | 34,210 | Full-text conversation archive + intelligent retrieval |
| dag_engine.py | 27,337 | DAG-based multi-agent workflow engine |
| experience.py | 24,915 | Long-term learned patterns with anti-fragile dual-write |
| cognifold.py | 22,994 | Autonomous knowledge structure emergence |
| dag_agents.py | 21,646 | Pre-built DAG agent functions (audit, test, report) |
| dag_orchestrator.py | 17,796 | DAG execution orchestrator |
| auto_learn.py | 17,281 | Autonomous learning pipeline (RSS, mirror gradient) |
| toolkit.py | 15,626 | Tool auto-registration, routing, schema |
| scheduler.py | 15,459 | Cron-based async job scheduler |
| lifeline.py | 14,901 | Agent health monitoring and crash recovery |
| evolution_engine.py | 14,816 | RSI self-evaluation + auto-rollback |
| rss_fetcher.py | 14,529 | RSS feed fetching and dedup |
| session.py | 14,147 | JSONL session manager with compression markers |
| pipeline.py | 13,708 | Data processing pipeline |
| daily_memory.py | 12,622 | Cross-session memory injection, daily consolidation |
| project_memory.py | 13,451 | Per-project persistent memory |
| skill_router.py | 12,612 | Skill trigger matching + capability routing |
| sleep_cycle.py | 10,076 | Offline maintenance (compression, pruning) |
| storage.py | 10,063 | SQLite storage abstraction |
| sandbox_safety.py | 8,430 | File validation, sandbox execution |
| tracer.py | 7,814 | Request tracing and metrics |
| skill_loader.py | 5,966 | Skill YAML loading and indexing |
| backup.py | 5,836 | Automated backup and recovery |
| loop_cache.py | 5,820 | Caching layer for recurring patterns |
| fetcher.py | 5,696 | URL fetching with retry + timeout |
| battle_protocol.py | 5,429 | Multi-agent debate protocol |
| village_connector.py | 5,093 | Village OS protocol connector |
| identity.py | 4,442 | Agent personality and rules |
| exec.py | 3,390 | Safe shell execution |
| safe_shell.py | 3,202 | Restricted shell with timeout + output capture |
| territory.py | 1,529 | Cross-agent access control |

### Tools (`tools/`)

| Module | Size | Purpose |
|--------|------|---------|
| hive_mind.py | 68,820 | Autonomous multi-engine deep research |
| distill.py | 22,184 | Model distillation pipeline |
| search.py | 20,707 | Multi-engine search aggregator |
| honeycomb_search.py | 18,873 | Distributed search across 3+ engines |
| gen_pro_report.py | 16,337 | Professional report generation |
| knowledge.py | 16,107 | Knowledge base CRUD and retrieval |
| self_edit.py | 14,589 | Safe code self-modification |
| qa_check.py | 14,584 | Memory quality evaluation suite |
| yf_image_tools.py | 12,695 | Image generation (SiliconFlow API) |
| forge_verify.py | 11,235 | Black-box test verification |
| trident_tools.py | 9,470 | Trident architecture orchestration |
| cua_tools.py | 6,696 | Computer-Using Agent tools |
| note_tool.py | 6,555 | L4 persistent note system |
| learn.py | 6,236 | Knowledge injection from conversation |
| feishu_send_file.py | 5,256 | Feishu file attachment sending |
| search_bridge.py | 5,233 | ProSearch bridge for Opprime |
| laser_doc.py | 5,451 | Documentation analysis + white-box audit |
| pdf_gen.py | 4,379 | PDF generation (WeasyPrint) |
| write_file.py | 4,784 | File write with territory check |
| read_file.py | 3,873 | File read with territory logging |
| self_search.py | 3,846 | ArchiveStore self-search |
| test_generator.py | 3,643 | Automated test case generation |
| cron.py | 3,573 | Cron task management tool |
| anchor_keeper.py | 3,564 | Project anchor file management |
| anysearch_tool.py | 3,514 | AnySearch bridge |
| xlsx_gen.py | 3,234 | Excel spreadsheet generation |
| feishu_card.py | 3,172 | Feishu interactive card builder |
| security_watch.py | 2,700 | Security scanning and monitoring |
| schema_tools.py | 2,830 | Tool schema generation |
| docx_gen.py | 2,928 | Word document generation |
| mail.py | 2,564 | Email sending via Opprime World |
| network_tools.py | 2,529 | Network utilities (ping, DNS, whois) |
| memory_profiler.py | 2,255 | Mirror/archive memory profiling |
| reminder.py | 1,894 | Scheduled reminders with persistence |
| rollback.py | 1,933 | Self-modification rollback |

### Channel Support (`lib/channels/`)

| Module | Size | Purpose |
|--------|------|---------|
| feishu.py | ~8,000 | Full Feishu/Lark Bot integration |

---

*GBase v0.4.0 — Built on Mac Studio M4 MAX with DeepSeek-chat. MIT Licensed.*
