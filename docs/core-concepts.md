# Core Concepts

## Mirror Memory

Every GBase agent has a persistent long-term memory stored in a SQLite database (`mirror.db`). It doesn't just remember recent conversations — it recalls what worked weeks ago.

**Key features:**
- Active recall: automatically injects relevant memories into each interaction
- Experience evolution: successful patterns are reinforced, failures are learned from
- Multi-agent isolation: each armor (hammer, ink, bumblebee, etc.) has its own mirror

## Thinking Module (v0.6+)

Before v0.6, GBase executed. Now it thinks first.

Every interaction passes through a structured reasonin pipeline embedded in the kernel:

| Lever | What it does |
|-------|-------------|
| **L0 Context Scan** | Scans the target directory, maps dependencies, flags risk zones |
| **L1 Thinking Router** | Classifies task type — code, design, research, logic — routes optimally |
| **L3 Verification** | Post-execution: syntax check, SQL validation, logical consistency |
| **L4 Reflection** | Quality self-check. Decides to refine or ship |

## Experience Engine

Every interaction is distilled into structured patterns — not raw logs, but queryable knowledge. The experience engine:
- Extracts patterns from successful and failed interactions
- Builds a reusable knowledge base over time
- Automatically injects relevant experience into new tasks

## Tool Auto-Registration

Define a tool with `@tool`:

```python
from gbase.toolkit import tool

@tool
def fetch_weather(city: str) -> dict:
    \"\"\"Get weather for a city.\"\"\"
    return {"city": city, "temp": 22}
```

That's it. The framework finds it, registers it, and exposes it to the LLM automatically.

## Identity System

GBase supports multiple identities per instance. Each identity has:
- A system prompt
- Tool sets
- Separate memory and experience

Available editions: Hacker, Prime, Standard, Lite.

## RSI (Recursive Self-Improvement)

The agent evaluates its own performance, detects failure patterns, and proposes improvements to its system prompt. Call `full_evolution_cycle()` to trigger.

## Editions

| Edition | Modules | Tools | Best for |
|---------|---------|-------|----------|
| Hacker | Full | 40+ | Power users, developers |
| Prime | Core + Memory | 25+ | Production deployments |
| Standard | Core | 15+ | Quick projects |
| Lite | Minimal | 8+ | Embedded / edge |
