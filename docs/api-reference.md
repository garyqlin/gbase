# API Reference

> **Coming soon.** The full API reference will cover:

## Kernel

- `Kernel(client, model, system_prompt, ...)` — main agent loop
- `kernel.run(user_input, platform="cli")` — synchronous interaction
- `kernel.ask(user_input, stream=True)` — streaming interaction

## Memory

- `Mirror(db_path)` — persistent long-term memory
- `ExperienceEngine(storage)` — experience distillation
- `ArchiveStore(storage)` — conversation archive

## Tools

- `@tool` — decorator for auto-registration
- `tools/__init__.py` — keyword-based tool routing
- Built-in tools: 40+ including search, file operations, code execution, and more

## Editions

- `get_edition(name)` — load edition config
- Editions: hacker, prime, standard, lite

## Thinking Module

- `gbase.thinking.router` — L1 Thinking Router
- `gbase.thinking.context` — L0 Context Scanner
- `gbase.thinking.reflection` — L4 Reflection Engine
- `gbase.thinking.execution` — L3 Verification Engine
- `gbase.thinking.middleware` — Kernel integration layer
