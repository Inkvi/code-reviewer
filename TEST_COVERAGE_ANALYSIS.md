# Test Coverage Analysis

**Generated:** 2026-03-05
**Overall coverage:** 67% (549 missed statements out of 1,670)

## Coverage by Module

| Module | Stmts | Miss | Cover | Priority |
|--------|-------|------|-------|----------|
| `reviewers/codex_agents_sdk.py` | 107 | 93 | **13%** | Critical |
| `reviewers/claude_sdk.py` | 57 | 47 | **18%** | Critical |
| `reviewers/reconcile.py` | 35 | 26 | **26%** | Critical |
| `shell.py` | 34 | 23 | **32%** | High |
| `cli.py` | 166 | 96 | **42%** | High |
| `workspace.py` | 31 | 17 | **45%** | High |
| `reviewers/codex_cli.py` | 128 | 56 | **56%** | Medium |
| `daemon.py` | 55 | 23 | **58%** | Medium |
| `reviewers/gemini_cli.py` | 105 | 32 | **70%** | Medium |
| `preflight.py` | 69 | 20 | **71%** | Low |
| `state.py` | 77 | 17 | **78%** | Low |
| `github.py` | 169 | 36 | **79%** | Low |
| `processor.py` | 303 | 50 | **83%** | Low |
| `config.py` | 193 | 5 | 97% | Done |
| `output.py` | 51 | 0 | 100% | Done |
| `review_decision.py` | 9 | 0 | 100% | Done |

## Existing Test Issues

1. **3 failing tests in `test_reconcile.py`** — The async tests use `@pytest.mark.asyncio` but `pytest-asyncio` is not installed (only listed as a dev dependency). These tests never actually run.

## Recommended Improvements (by priority)

### 1. Reviewer backends (13-26% coverage) — Critical

These are the core value-producing modules and have almost no tests.

**`reviewers/claude_sdk.py` (18%)**
Only utility-level functions are lightly covered. Missing tests for:
- `_collect_text_from_assistant()` — extracting text from multi-block assistant messages
- `_extract_token_usage()` — handling missing/malformed usage dicts, zero-token cases
- `_run_claude_prompt()` — timeout behavior (`fail_after`), empty response error, merging parts vs final_result precedence
- `run_claude_review()` — success path producing `ReviewerOutput`, exception path (status="error")

**`reviewers/codex_agents_sdk.py` (13%)**
Virtually untested. Missing tests for:
- `_load_agents_sdk()` — dynamic import fallback chain (`agents` → `openai_agents` → error)
- `_invoke_runner_sync()` — `run_sync` vs `run` fallback, awaitable result handling, missing method error
- `_extract_token_usage()` and `_extract_result_markdown()` — attribute-based and dict-based extraction paths
- `_build_agent_model_settings()` — signature introspection for `reasoning` vs `reasoning_effort` parameters
- `run_codex_review_via_agents_sdk()` — success, timeout, and generic exception paths

**`reviewers/reconcile.py` (26%)**
The existing 3 tests are all broken (pytest-asyncio not installed). Missing tests for:
- `_format_source()` — failed vs ok output formatting
- `_format_pr_comments()` — empty vs populated comment lists
- `reconcile_reviews()` — prompt construction with multiple reviewer outputs, backend dispatch (claude/codex/gemini), unknown backend error

### 2. Shell & Workspace (32-45% coverage) — High

**`shell.py` (32%)**
Core infrastructure with no tests at all. Missing tests for:
- `CommandError` — exception message formatting
- `run_command()` — successful execution, non-zero exit with `check=True` raising `CommandError`, `check=False` returning failure gracefully, timeout behavior
- `run_json()` — valid JSON parsing, invalid JSON raising `RuntimeError`
- `run_command_async()` — async execution, timeout causing process kill

**`workspace.py` (45%)**
Missing tests for:
- `PRWorkspace.prepare()` — directory creation, git clone/fetch/checkout sequence, cleanup on failure
- `PRWorkspace.update_to_latest()` — re-fetch and checkout
- `PRWorkspace.cleanup()` — `keep=True` skips deletion, `keep=False` removes directory

### 3. CLI commands (42% coverage) — High

**`cli.py` (42%)**
The override helper functions are tested but all three commands (`check`, `run-once`, `start`) are untested. Missing tests for:
- `_resolve_reconciler_settings()` — backend-specific model/effort resolution for claude/codex/gemini
- `_load_runtime()` — full override application chain, state store initialization
- `_target_pr_urls_for_run_once()` — deduplication, `--use-saved-review` without `--pr-url` error
- `check_command()` — preflight execution and table output
- `run_once_command()` — targeted PR processing vs cycle mode, store lock release in finally
- `start_command()` — daemon startup, KeyboardInterrupt handling, error exit code

### 4. Daemon & remaining reviewer modules (56-70%) — Medium

**`daemon.py` (58%)**
- `start_daemon()` — polling loop behavior, cycle error handling, sleep between cycles
- Full `run_cycle()` with mixed success/failure candidates

**`reviewers/codex_cli.py` (56%)**
- `run_codex_review()` — end-to-end review execution, JSON mode vs plain text fallback, error handling
- `run_codex_prompt()` — prompt execution for reconciliation use case

**`reviewers/gemini_cli.py` (70%)**
- `run_gemini_review()` — end-to-end review execution, JSON parsing failures
- `run_gemini_prompt()` — prompt execution for reconciliation

### 5. Processor edge cases (83% coverage) — Low

**`processor.py` (83%)**
Well tested overall, but missing coverage for:
- Mid-review commit detection and restart logic (`_NewCommitDetected`, `_run_reviewers_with_monitoring`)
- `max_mid_review_restarts` exceeded path
- Auto-submit review decision posting
- `use_saved_review` reuse path
- Token usage aggregation logging

## Quick Wins

These would give the most coverage improvement for the least effort:

1. **Fix `test_reconcile.py`** — Install `pytest-asyncio` properly and fix the 3 broken tests. This immediately recovers reconcile coverage from 26% to ~70%+.

2. **Add `shell.py` unit tests** — Pure functions with no external dependencies to mock. Test `CommandError`, `run_command` (mock `subprocess.run`), `run_json` (mock `run_command`). ~15 minutes of work for 32% → 90%+.

3. **Add `workspace.py` unit tests** — Mock `run_command` and `shutil.rmtree`. Test `prepare()` happy path, failure cleanup, `update_to_latest()`, and `cleanup()` with `keep=True/False`. ~20 minutes for 45% → 90%+.

4. **Add `claude_sdk.py` helper tests** — `_collect_text_from_assistant` and `_extract_token_usage` are pure functions that can be tested with simple mock objects. ~10 minutes for meaningful coverage gains.

5. **Add `codex_agents_sdk.py` helper tests** — `_extract_result_markdown`, `_extract_token_usage`, `_invoke_runner_sync`, `_build_agent_model_settings` are all testable with mock objects and no real SDK needed.
