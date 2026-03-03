# Repository Guidelines

## Project Structure & Module Organization
This repository uses a `src/` layout.
- `src/pr_reviewer/`: core application logic (CLI, daemon loop, GitHub integration, config/state, output).
- `src/pr_reviewer/reviewers/`: reviewer backends and reconciliation helpers (`claude_sdk`, `codex_cli`, `codex_agents_sdk`).
- `tests/`: pytest suite, generally mirrored by module name (for example, `test_processor.py` for `processor.py`).
- `reviews/<org>/<repo>/`: generated review artifacts (`.md` and `.raw.md`), not source code.
- `config.example.toml`: baseline config template for local setup.

## Build, Test, and Development Commands
- `uv sync --extra dev`: install runtime + dev dependencies.
- `uv run pr-reviewer check`: run preflight checks and print runtime config summary.
- `uv run pr-reviewer run-once`: execute one polling/review cycle.
- `uv run pr-reviewer run-once --pr-url <PR_URL>`: review specific PR URL(s) directly.
- `uv run pr-reviewer run-once --pr-url <PR_URL> --force`: bypass normal skip checks for specific PR URL(s).
- `uv run pr-reviewer start`: run the daemon continuously.
- `uv run ruff check .`: lint.
- `uv run ruff format .`: format code.
- `uv run pytest`: run tests (`-q` is configured in `pyproject.toml`).

## Coding Style & Naming Conventions
- Python 3.12+, 4-space indentation, and explicit type hints on public functions.
- Follow Ruff settings in `pyproject.toml` (`line-length = 100`, rules `E,F,I,UP,B,W`).
- Use `snake_case` for modules/functions/variables, `PascalCase` for classes, and concise Typer command names.
- Keep CLI orchestration in `cli.py`; isolate reusable logic in testable modules.

## Testing Guidelines
- Framework: `pytest` with `pytest-asyncio`.
- Test files use `tests/test_*.py`; test names should describe behavior (`test_<action>_<expected_result>`).
- Add or update tests for every behavior change, especially CLI flags, skip logic, state handling, and reviewer reconciliation.
- Run targeted tests during iteration, for example: `uv run pytest tests/test_processor.py`.

## Commit & Pull Request Guidelines
- Prefer imperative commit subjects; Conventional Commit prefixes are used in history (`feat:`, `fix:`) and are recommended.
- Keep each commit focused on one logical change.
- PRs should include: what changed, why it changed, config impact (`config.toml` keys), and validation steps run (`ruff`, `pytest`).
- Link related issues and include relevant CLI/log output when behavior changes.

## Security & Configuration Tips
- Never commit secrets or local credentials in `config.toml`.
- Document new config keys in `config.example.toml` and `README.md`.
- Review generated `reviews/` content before sharing; it may contain repository-sensitive context.
