# pr-reviewer

A Python daemon that monitors GitHub pull requests and generates reconciled reviews using:
- Claude Agent SDK (`/review <PR_URL>`)
- Codex CLI (`codex review`)

## Requirements

- Python 3.12+
- `uv`
- `gh` authenticated (`gh auth login`)
- `codex` authenticated
- `claude` authenticated (Agent SDK depends on Claude Code runtime)

## Setup

```bash
uv sync --extra dev
cp config.example.toml config.toml
```

## Commands

```bash
uv run pr-reviewer check --config config.toml
uv run pr-reviewer run-once --config config.toml
uv run pr-reviewer start --config config.toml
```

## Behavior

- Polls open PRs in `github_org` where `review-requested:@me`
- Skips draft PRs and (by default) PRs authored by you
- Skips PRs when you already posted an issue comment
- Runs Claude and Codex review in parallel
- Reconciles with Claude and writes:
  `reviews/<org>/<repo>/pr-<number>.md`
- Prints file path when ready
- Optional posting when `auto_post_review = true`

## Lint and test

```bash
uv run ruff check .
uv run ruff format .
uv run pytest
```
