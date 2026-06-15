# Changelog

## [v0.4.1] - 2026-06-15

### Added
- Archive Store: bidirectional memory read/write with semantic search
- GKM (GBase Knowledge Map): FTS5 + bidirectional link knowledge graph
- Experience Engine: batch flush via cron (12:00/20:00), JSONL aggregated pipeline
- arms SDK: unified `/ask` protocol for all agents
- launchd daemon management: 14 agent ports with KeepAlive + ThrottleInterval

### Changed
- Memory system: disabled automatic compression (compression self-accelerating loop fixed)
- Session rows: MAX_SESSION_ROWS=100000 (was 15)
- Kernel prompt: stripped to core layers (Hot Mirror + Warm Recall + Knowledge FTS + Archive Store)
- Dead code sweep: 22 modules removed, gbase-lib reduced from 43 to 21 modules
- Foundation chassis v1.0.0: core 16 files isolated from plugin layer

### Removed
- All gate/guard/breaker systems (floodgate, tool budget, NER, plan system)
- Cognifold cognitive engine (overengineering)
- Neocortex module (1,159 lines, never referenced)
- DailyMemory engine (never ran in production)

## [v0.3.4] - 2026-06-09

### Added
- Memory cascade hierarchy: Hot Mirror → Warm Recall → Cold Knowledge
- Glink v0.5: event-driven main bus + project management
- Opprime Emotion: dual-hemisphere emotional recognition (port 4210)
- Seven-arm Gundam fleet: hammer/ink/bumblebee/laser/forge/lancer-cc/lancer-x

### Changed
- Full model migration: all agents → 阿里云百炼 (qwen3.7-plus, deepseek-v4-flash, GLM-5.1)
- Mirror: 3-tier filter + prompt slim (95K → 8K tokens)
- SessionManager: JSONL → hybrid SQLite + file store

## [v0.2.2] - 2026-05-25

### Added
- First public release
- Agent identity system with SOUL.md
- 40+ auto-registered tools
- Mirror memory with self-improvement
- WebChat interface
- CI pipeline (ruff/codespell/mypy)
