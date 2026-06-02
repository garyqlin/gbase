# Changelog

## [0.4.0] - 2026-06-02

### Added
- ArchiveStore: full-text conversation archive with LIKE-based semantic retrieval (replaces LLM compression)
- Session: removed 3-layer LLM compression, replaced with ArchiveStore append/search
- Territory safety: cross-agent read/write access control (write blocking, read warning)
- RSI Dual-Knob: task intent classification → dynamic temperature control
- Time-decay weighted retrieval: M3 sparse attention inspired (7d full / 7-30d linear / 30d+ exponential)
- Entity conflict detection: Cosmos 3 inspired, auto-detect contradictions on write
- Hot query cache (LRU, max 64) for high-frequency entity lookups
- Archive trash recovery: deleted entries saved to `data/archive_trash/` as grep-able JSONL
- L4 permanent notes: auto-serialize when tool calls >= 5 or reply >= 500 chars
- 20+ new tools: self_edit, feishu_send, note_tool, knowledge, mirror_tool, chain, mail, security_watch, etc.

### Changed
- kernel.py: archive_store init + semantic bridge search/save (disabled old online LLM compression)
- session.py: replaced compress_l1/l2/async_compress with archive-driven context building
- mirror.py: OR-style token expansion recall (jieba-based), search_cold() for forgotten memories
- toolkit.py: compact tool list indexing (5K→1.2K chars)

### Fixed
- Compress method `compress()` never called (async daemon httpx silent failure)
- Session context: removed irreversible degradation logic (prevents compression death spiral)

## [0.3.1] - 2026-05-28

### Added
- Mirror engine: triple-layer injection filter (intent score + feedback ratio + density)
- Mirror engine: OR-style token expansion recall (jieba-based, replaces single LIKE)
- Mirror engine: `search_cold()` for forgotten memory retrieval
- Kernel: `_current_user_message` injected into mirror's `get_injection_text` for intent matching
- AutoLearner: signature compatibility (`sender_func=None, kernel_run_func=None`)

### Fixed
- F-string Python 3.11 compatibility in `recall()`
- Removed internal forge-audit files from release tree
- database migration: `inject_hits` column for triple-layer filter tracking

### Changed
- Mirror `get_injection_text()` accepts optional `user_input` parameter
- Mirror `recall()` accepts `include_forgotten` optional parameter
