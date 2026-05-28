# Changelog

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
