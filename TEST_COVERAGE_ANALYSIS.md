# Test Coverage Analysis

**Date:** 2026-03-06
**Overall coverage:** 68% (741 of 2337 statements missed)
**Total tests:** 185 across 16 test files
**Failing tests:** 6 (pre-existing — config defaults, reconcile backends, CLI output format)

---

## Coverage by Module

| Module | Stmts | Miss | Cover | Priority |
|--------|-------|------|-------|----------|
| `reviewers/codex_agents_sdk.py` | 117 | 103 | **12%** | HIGH |
| `reviewers/claude_sdk.py` | 66 | 55 | **17%** | HIGH |
| `reviewers/reconcile.py` | 46 | 34 | **26%** | HIGH |
| `cli.py` | 278 | 190 | **32%** | HIGH |
| `workspace.py` | 31 | 17 | **45%** | HIGH |
| `shell.py` | 54 | 27 | **50%** | HIGH |
| `reviewers/codex_cli.py` | 132 | 57 | 57% | MEDIUM |
| `daemon.py` | 67 | 24 | 64% | MEDIUM |
| `preflight.py` | 69 | 20 | 71% | MEDIUM |
| `reviewers/gemini_cli.py` | 105 | 29 | 72% | MEDIUM |
| `state.py` | 78 | 17 | 78% | LOW |
| `models.py` | 105 | 20 | 81% | LOW |
| `github.py` | 242 | 43 | 82% | MEDIUM |
| `processor.py` | 421 | 73 | 83% | MEDIUM |
| `local_review.py` | 95 | 13 | 86% | LOW |
| `reviewers/triage.py` | 56 | 6 | 89% | LOW |
| `reviewers/lightweight.py` | 24 | 1 | 96% | LOW |
| `config.py` | 247 | 7 | 97% | LOW |
| `output.py` | 68 | 0 | 100% | — |
| `review_decision.py` | 9 | 0 | 100% | — |

---

## Recommended Improvements (prioritized)

### 1. `reviewers/codex_agents_sdk.py` — 12% coverage (NO dedicated tests)

This module has zero dedicated tests. All 117 statements related to the OpenAI Agents SDK backend are untested.

**What to test:**
- `_build_review_instructions()` — prompt construction with PR metadata
- `_create_review_agent()` — agent configuration with model and tools
- `_extract_review_from_events()` — event stream parsing for review text
- `_extract_token_usage_from_result()` — token usage extraction from SDK result objects
- `run_codex_review_via_agents_sdk()` — end-to-end with mocked SDK, including timeout and error paths

**Approach:** Mock the `openai.agents` SDK classes (`Agent`, `Runner`) and verify prompt construction, tool configuration, and output parsing without making real API calls.

---

### 2. `reviewers/claude_sdk.py` — 17% coverage (MINIMAL tests)

Only the import path is exercised. The core review execution logic is entirely untested.

**What to test:**
- `_collect_text_from_assistant()` — extracting text from `AssistantMessage` blocks (single block, multiple blocks, non-text blocks)
- `_extract_token_usage()` — parsing `ResultMessage.usage` into `TokenUsage`
- `_run_claude_prompt()` — timeout handling, empty response, error propagation
- `run_claude_review()` — prompt construction for GitHub PR vs local review, reviewer output assembly

**Approach:** Mock `claude_agent_sdk.run()` to return canned `ResultMessage` objects. Test prompt construction separately from execution.

---

### 3. `reviewers/reconcile.py` — 26% coverage (3 failing tests)

The existing tests use `pytest.mark.asyncio` without the `pytest-asyncio` plugin installed (they should use `asyncio.run()` per project conventions). The core reconciliation logic including prompt building, sanitization, and formatting is untested.

**What to test:**
- `_escape_delimiters()` — escaping `<untrusted_data>` tags in reviewer output
- `_sanitize_comment()` — filtering suspicious prompt-injection patterns from PR comments
- `_format_source()` — formatting individual reviewer outputs with proper labeling
- `_format_pr_comments()` — handling `None`, empty lists, and comment lists
- `reconcile_reviews()` — all three backend paths (claude/codex/gemini), max_findings/max_test_gaps params

**Approach:** Fix existing tests to use `asyncio.run()`. Add unit tests for the helper functions which are pure functions and easy to test.

---

### 4. `cli.py` — 32% coverage (command execution untested)

The override/config functions are well-tested (24 tests), but none of the actual CLI command handlers are tested.

**What to test:**
- `_load_runtime()` — config loading, override application, preflight execution
- `check` command — output when preflight passes/fails
- `run_once` command — single PR processing flow
- `local_review` command — local diff review flow
- `daemon` command — daemon startup with interval

**Approach:** Use Typer's `CliRunner` to invoke commands with mocked dependencies. Focus on argument parsing and error handling rather than full integration.

---

### 5. `workspace.py` — 45% coverage (NO dedicated tests)

The workspace lifecycle (clone, checkout, cleanup) has no dedicated tests. It's only indirectly exercised via mocked `DummyWorkspace` in processor tests.

**What to test:**
- `prepare()` — successful clone + checkout, clone failure, checkout failure, directory already exists
- `update_to_latest()` — successful fetch + checkout, fetch failure, SHA mismatch
- `cleanup()` — removal when `keep=False`, preservation when `keep=True`, removal failure (permissions)

**Approach:** Use `tmp_path` fixtures with real git repos (similar to `test_local_review.py` which already does this pattern well).

---

### 6. `shell.py` — 50% coverage (NO dedicated tests)

The command execution layer — retry logic, throttling, error handling — is completely untested directly.

**What to test:**
- `run_command()` — successful execution, non-zero exit, retry on failure, max retries exhaustion
- `run_json()` — valid JSON parsing, malformed JSON error
- `run_command_async()` — successful async execution, timeout triggering, process cleanup after timeout
- `_gh_throttle()` — minimum interval enforcement between `gh` calls
- `CommandError` — attribute access (args_list, code, stdout, stderr)

**Approach:** Mock `subprocess.run` and `asyncio.create_subprocess_exec`. For throttle testing, use `time.monotonic()` assertions.

---

### 7. `github.py` — 82% but many methods untested

Coverage is decent overall but 15+ methods have zero test coverage.

**What to test (highest value):**
- `get_pr_issue_comments()` — pagination loop, `max_comments` limit, comment truncation
- `_is_slash_command_authorized()` — PR author path, org member path, unauthorized user
- `_find_latest_review_command()` — multiple comments ordering, authorized vs unauthorized
- `discover_slash_command_candidates()` — full flow with mocked API
- `post_pr_comment()`, `submit_pr_review()` — verify correct `gh` CLI args

**Approach:** Follow existing test patterns using `monkeypatch` on `shell.run_json`/`shell.run_command`.

---

### 8. `processor.py` — 83% but critical paths missed

The happy path is well-tested, but error handling and edge cases in the processing pipeline are not.

**What to test:**
- `_validate_review_format()` — missing `### Findings`, missing `### Test Gaps`
- `_publish_and_persist()` — posting failure recovery, state save on error
- `_check_pr_head_changed()` — new commit detected mid-review, fetch failure
- `_run_reviewers_with_monitoring()` — `_NewCommitDetected` exception, task cancellation
- Restart loop — `max_mid_review_restarts` exhaustion
- Slash command reply posting flow

**Approach:** Extend existing test helpers (`DummyStore`, `DummyWorkspace`, monkeypatched reviewers) to cover error/edge cases.

---

### 9. `daemon.py` — 64% coverage

The cycle execution and parallel processing logic are untested.

**What to test:**
- `run_cycle()` — merging normal + slash command candidates, parallel processing with semaphore, exception handling per-candidate
- Return value accuracy (processed count vs total)

**Approach:** Mock `GitHubClient.discover_pr_candidates()` and `process_candidate()` to test orchestration logic.

---

### 10. `models.py` — 81% coverage

Data model serialization/deserialization paths are partially untested.

**What to test:**
- `TokenUsage.__add__()` — addition of two usage objects
- `ReviewerOutput.duration_seconds` property
- `ProcessingResult.to_dict()` — serialization with various field combinations
- `ProcessedState` — round-trip with all fields

---

## Pre-existing Test Issues

1. **6 failing tests** — `test_config.py` (2), `test_reconcile.py` (3), `test_cli.py` (1) are failing, likely due to source code changes that haven't been reflected in tests yet.
2. **`test_reconcile.py` uses `pytest.mark.asyncio`** but the project convention is `asyncio.run()` and `pytest-asyncio` is not installed.
3. **No integration tests** — there are no tests that exercise the full pipeline (discover → triage → review → reconcile → post).

## Quick Wins

These improvements offer the most coverage gain for the least effort:

1. **Fix the 6 failing tests** — get back to green before adding new tests
2. **Add `shell.py` tests** — pure infrastructure, easy to mock, high blast radius if broken
3. **Add `workspace.py` tests** — follows existing `test_local_review.py` git-repo patterns
4. **Add reconcile helper tests** — `_escape_delimiters`, `_sanitize_comment` are pure functions
5. **Add `models.py` serialization tests** — dataclass round-trips are trivial to write
