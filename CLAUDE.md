# code-reviewer Project

## Environment
- Python 3.12+, 4-space indentation, explicit type hints on public functions
- Use `python3` not `python` (system has no `python` alias)
- Use `uv run ruff` for linting, `uv run ruff format .` for formatting
- Use `uv run pytest` for tests (`-q` configured in `pyproject.toml`)
- 4 pre-existing E501 violations in processor.py and test_processor.py — ignore these
- Follow Ruff settings in `pyproject.toml` (`line-length = 100`, rules `E,F,I,UP,B,W`)

## Project Structure
- `src/code_reviewer/`: core application logic (CLI, daemon, GitHub integration, config/state, output)
- `src/code_reviewer/reviewers/`: reviewer backends and reconciliation (`claude_sdk`, `codex_cli`, `codex_agents_sdk`, `gemini_cli`, `triage`, `lightweight`, `reconcile`)
- `tests/`: pytest suite mirrored by module name (e.g., `test_processor.py` for `processor.py`)
- `reviews/<org>/<repo>/`: latest review artifacts (`pr-<number>.md`, `pr-<number>.raw.md`) plus versioned history under `pr-<number>/`
- Config: `pyproject.toml`, `config.example.toml`
- All model interactions go through CLI/SDK runners (`_run_claude_prompt`, `run_codex_prompt`, `run_gemini_prompt`) — no direct API calls

## Code Patterns
- All `gh` CLI calls go through `shell.py` (`run_command`, `run_json`, `run_command_async`) with a global throttle (`_GH_MIN_INTERVAL`) to avoid GitHub rate limits
- `process_candidate()` returns `ProcessingResult` dataclass (not bool); use `.processed` for the boolean
- Prompt template files (`triage.py`, `lightweight.py`, `reconcile.py`) have E501 ignored via pyproject.toml per-file-ignores
- Config fields use Pydantic field validators; cross-field validation uses model validators
- CLI overrides follow `_apply_field_override` pattern in `cli.py`
- Backend functions support claude/codex/gemini with graceful fallback on errors
- Keep CLI orchestration in `cli.py`; isolate reusable logic in testable modules

## Testing
- Changing `process_candidate` return type requires updating test_processor.py, test_daemon.py, and test_slash_command.py
- Tests use `DummyStore`, `DummyWorkspace`, `GitHubClient` with monkeypatch
- Processor tests that call `process_candidate` must mock `run_triage` (use `_mock_triage_full_review` helper)
- Use `asyncio.run()` to test async functions (not pytest-asyncio markers)
- Add or update tests for every behavior change, especially trigger-state decisions, state handling, CLI behavior, and reviewer reconciliation

## Commands
- `uv sync --extra dev`: install runtime + dev dependencies
- `uv run code-reviewer check`: preflight checks and runtime config summary
- `uv run code-reviewer run-once`: one polling/review cycle
- `uv run code-reviewer run-once --pr-url <URL>`: review specific PR(s)
- `uv run code-reviewer review --uncommitted`: review local uncommitted changes
- `uv run code-reviewer review --base main`: compare branches locally
- `uv run code-reviewer start`: run daemon continuously

## Commits & PRs
- Imperative commit subjects; Conventional Commit prefixes (`feat:`, `fix:`, `docs:`)
- Keep each commit focused on one logical change
- Document new config keys in `config.example.toml` and `README.md`
- Never commit secrets or local credentials in `config.toml`
