# pr-reviewer Project

## Environment
- Use `python3` not `python` (system has no `python` alias)
- Use `uv run ruff` for linting, `python3 -m pytest` for tests
- 4 pre-existing E501 violations in processor.py and test_processor.py — ignore these

## Project Structure
- Source: `src/pr_reviewer/`
- Tests: `tests/`
- Config: `pyproject.toml`, `config.example.toml`
- All model interactions go through CLI/SDK runners (`_run_claude_prompt`, `run_codex_prompt`, `run_gemini_prompt`) — no direct API calls

## Code Patterns
- All `gh` CLI calls go through `shell.py` (`run_command`, `run_json`, `run_command_async`) with a global throttle (`_GH_MIN_INTERVAL`) to avoid GitHub rate limits
- `process_candidate()` returns `ProcessingResult` dataclass (not bool); use `.processed` for the boolean
- Prompt template files (`triage.py`, `lightweight.py`, `reconcile.py`) have E501 ignored via pyproject.toml per-file-ignores
- Config fields use Pydantic field validators; cross-field validation uses model validators
- CLI overrides follow `_apply_field_override` pattern in `cli.py`
- Backend functions support claude/codex/gemini with graceful fallback on errors

## Testing
- Changing `process_candidate` return type requires updating test_processor.py, test_daemon.py, and test_slash_command.py
- Tests use `DummyStore`, `DummyWorkspace`, `GitHubClient` with monkeypatch
- Processor tests that call `process_candidate` must mock `run_triage` (use `_mock_triage_full_review` helper)
- Use `asyncio.run()` to test async functions (not pytest-asyncio markers)
