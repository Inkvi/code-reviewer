# pr-reviewer

A Python daemon that monitors GitHub pull requests and generates reconciled reviews using:
- Claude Agent SDK (`/review <PR_URL>`)
- Codex (`codex review` by default, or OpenAI Agents SDK experimental backend)

## Requirements

- Python 3.12+
- `uv`
- `gh` authenticated (`gh auth login`)
- `codex` authenticated
- `claude` authenticated (Agent SDK depends on Claude Code runtime)
- for `codex_backend = "agents_sdk"`: OpenAI Agents SDK package + `OPENAI_API_KEY`

## Setup

```bash
uv sync --extra dev
cp config.example.toml config.toml
```

Optional excludes (in `config.toml`):

```toml
# Exclude by full repo name and/or bare repo name
excluded_repos = ["polymerdao/infra", "sandbox-repo"]
```

Choose reviewers:

```toml
# Default: run both in parallel
enabled_reviewers = ["claude", "codex"]

# Codex-only mode
# enabled_reviewers = ["codex"]

# Claude-only mode
# enabled_reviewers = ["claude"]
```

Or override from CLI without editing config:

```bash
uv run pr-reviewer run-once --enabled-reviewer codex
uv run pr-reviewer start --enabled-reviewer claude --enabled-reviewer codex
```

Choose Codex backend:

```toml
# Stable default:
codex_backend = "cli"

# Experimental OpenAI Agents SDK backend:
# codex_backend = "agents_sdk"
# codex_model = "gpt-5.3-codex"
```

Model and reasoning tuning:

```toml
# Claude backend (review + reconciliation)
# claude_model = "claude-sonnet-4-5"
# claude_reasoning_effort = "low"    # low|medium|high|max

# Codex backend
codex_model = "gpt-5.3-codex"
codex_reasoning_effort = "low"   # default for local dev/test
# # low|medium|high
```

Or override backend from CLI:

```bash
uv run pr-reviewer run-once --enabled-reviewer codex --codex-backend cli
uv run pr-reviewer run-once --enabled-reviewer codex --codex-backend agents_sdk
uv run pr-reviewer run-once --codex-model gpt-5.3-codex --codex-reasoning-effort high
uv run pr-reviewer run-once --claude-model claude-sonnet-4-5 --claude-reasoning-effort medium
```

Optional auto submission:

```toml
# Post concise review as a normal PR comment
auto_post_review = false

# Submit formal review decision automatically:
# - approve when no P1/P2 findings
# - request changes when any P1/P2 finding exists
auto_submit_review_decision = false

# Include full STDERR streams in the raw sidecar output
include_reviewer_stderr = true
```

## Commands

```bash
uv run pr-reviewer check
uv run pr-reviewer run-once
uv run pr-reviewer start
uv run pr-reviewer force --pr-url https://github.com/<org>/<repo>/pull/<number>
```

## Behavior

- Polls open PRs in `github_org` where `review-requested:@me`
- Excludes repos listed in `excluded_repos`
- Runs only reviewers listed in `enabled_reviewers`
- Uses selected Codex backend from `codex_backend`
- Codex CLI backend uses `codex review` and, when supported by CLI version, can parse JSON event output
- Skips draft PRs and (by default) PRs authored by you
- Skips PRs when you already posted an issue comment
- Skips PRs when a saved review markdown already exists for that PR
- Runs Claude and Codex review in parallel
- Reconciles with Claude and writes:
  `reviews/<org>/<repo>/pr-<number>.md`
- Saves raw Claude/Codex outputs to:
  `reviews/<org>/<repo>/pr-<number>.raw.md`
- Prints file path when ready
- Optional comment posting when `auto_post_review = true`
- Optional formal review submission when `auto_submit_review_decision = true`
- `force` command bypasses reviewer assignment discovery and skip checks (saved review + existing comment + head SHA)

## Lint and test

```bash
uv run ruff check .
uv run ruff format .
uv run pytest
```
