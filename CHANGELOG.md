# Changelog

## [0.6.1] — 2026-06-20

### 🐛 Fixed
- Import error in thinking/__init__.py:  →  (symbol mismatch)


## [0.6.0] — 2026-06-20

### 🌟 Thinking Lever Integration

Permanent embedding of the multi-layer structured thinking framework (`thinking-lever`) directly into GBase's kernel pipeline. Every GBase agent now benefits from structured reasoning without external dependencies.

**What's new:**
- **L0 Context Scanner** — Automatically extracts entities, actions, constraints, and risk profiles from user messages at pre_process time. Injects structured context into the conversation.
- **L1 Thinking Router** — Classifies task intent (diagnose/design/optimize/predict/execute) and selects appropriate thinking methods. Injects as [thinking_method: ...] markers.
- **L4 Reflection Engine** — Post-reply quality self-check. Evaluates completeness, clarity, accuracy, conciseness, and structural presentation. Optionally refines the output.

**Architecture:**
- New `gbase/thinking/` module: `router.py`, `context.py`, `reflection.py`, `execution.py`, `middleware.py`
- Zero new external dependencies. All logic is pure Python.
- Non-blocking: lever failures are silently logged and don't interrupt the main pipeline.
- Configurable: `self._thinking_lever_enabled` flag in Kernel.

**Benchmarks:**
- Design/optimization tasks: ~4.7x speed improvement (lever-based reasoning guides LLM directly to structured output)
- Simple tasks: automatically skipped (L1 skip detection)

## [0.5.2] — 2026-06-15
- CI fixes, dependency cleanup, search module formatting

## [0.5.1] — 2026-06-14
...

