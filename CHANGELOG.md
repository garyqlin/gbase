# Changelog

## [0.4.1] - 2026-06-09

### Added 🌐
- **Web Chat Interface**: `python3 main.py --mode web --port 8765` opens a full cyberpunk-themed browser UI
  - Streaming response (chunk-by-chunk)
  - File upload: drag & drop or click — images, PDFs, DOCX, XLSX, code files
  - Agent Mind panel: real-time knowledge hits, tool chain visibility, live metrics
  - Auto-reconnect WebSocket
  - Zero external dependencies (single HTML file, all CSS/JS inline)
- **lib/channels/webchat.py**: WebSocket chat channel with streaming, file processing, knowledge injection
- **webchat/index.html**: Production-ready frontend (23600 bytes, 0 npm/node/Webpack)
- FTS5 token filter: fixed `detail=column` issue where pure-digit/alpha tokens were parsed as column names (port from Opprime)
- Knowledge auto-retrieval: record_hit() on every auto-search hit for cache warmth
- _recorded_tc: track total tool calls per loop execution for SkillOpt gate integration
- _build_gmem_summary: standalone helper for archive_store compression summary construction
- tools/remember_info.py: unified memory route — auto-classifies user content into Knowledge/Notes/Experience
- tools/archive_search.py: Archive Store search tool for cross-session memory recall
- tools/glink_projects.py: project-level memory system for long-running tasks
- rules/AGENCY.md, rules/THINKING.md, rules/FINISH.md: behavior rules for agent autonomy

### Changed
- kernel.py: merged Opprime improvements (FTS5 safety filter + Knowledge hit recording + loop tc tracking + execution mode detector)
- session.py: synchronized with Opprime (fixes for L1 compaction type mismatch, image_url filtering)
- experience.py: synchronized with Opprime (improved JSON error tolerance)
- storage.py: synchronized with Opprime (aging mechanism for Knowledge entries)
- archive_store.py: generalized agent-specific DB fallback paths to ~/gbase-home/ (public-ready)
- main.py: --mode web flag added, shared common infra between Feishu and Web modes

### Fixed
- knowledge.py: FTS5 MATCH returning 0 matches due to unpurged detail=column token pollution
- session.py: content null leading to DeepSeek serde enum variant mismatch (400 error)
- session.py: L1 compression type mismatch (passing str instead of list[dict])
- session.py: image_url in V2 Feishu messages not filtered, causing 400 errors

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
