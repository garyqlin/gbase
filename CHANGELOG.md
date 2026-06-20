# Changelog

## [0.6.1] — 2026-06-20

### 🛡️ They attacked us. We shipped.

Same day three bot accounts dumped star farming accusations across our repo, we shipped the Thinking Module into the kernel. No arguments, no delays.

### 🚀 Added
- **`gbase` CLI** — `gbase init`, `gbase chat`, `gbase serve`, `gbase --version`. Works after `pip install gbase`. Entry point registered in pyproject.toml.
- **`docs/` directory** — 5 pages: getting started, installation, core concepts, CLI reference, API reference stub
- **`gbase/__init__.py` version** — `__version__ = "0.6.1"`

### 📝 Changed
- **README Quick Start** — pip install path added as primary option (clone kept as full framework path)
- **examples/01_hello_gbase.py** — replaced "clone-only" instructions with pip-first + clone alternative. Removed "GBase is not a pip library" comment

### 🐛 Fixed
- Import error in thinking/__init__.py (`verify_code` → `verify_result` symbol mismatch)
- 27 lint errors in `gbase/thinking/` (ARG001, PIE810, SIM102, F841, etc.) — CI now green across all three jobs


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

