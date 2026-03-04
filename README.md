# pr-reviewer

A Python daemon that monitors GitHub pull requests and generates reconciled reviews using:
- Claude Agent SDK (`/review <PR_URL>`)
- Codex (`codex review` by default, or OpenAI Agents SDK experimental backend)
- Gemini CLI (`/code-review` via `code-review` extension)

## Requirements

- Python 3.12+
- `uv`
- `gh` authenticated (`gh auth login`)
- `codex` authenticated
- `claude` authenticated (Agent SDK depends on Claude Code runtime)
- if using `gemini` reviewer: `gemini` authenticated + `code-review` extension installed
  (`gemini extensions install https://github.com/gemini-cli-extensions/code-review`)
- for `codex_backend = "agents_sdk"`: OpenAI Agents SDK package + `OPENAI_API_KEY`

## Setup

```bash
uv sync --extra dev
cp config.example.toml config.toml
```

Optional excludes (in `config.toml`):

```toml
# Owner scopes to monitor (orgs and/or usernames for personal repos)
github_orgs = ["Inkvi"]

# Add additional owners as needed
# github_orgs = ["polymerdao", "another-org", "Inkvi"]

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
# Claude reviewer backend
# claude_model = "claude-sonnet-4-5"
# claude_reasoning_effort = "low"    # low|medium|high|max

# Reconciler backend (claude|codex|gemini)
reconciler_backend = "claude"
# falls back by backend when unset:
# - claude -> claude_model / claude_reasoning_effort
# - codex  -> codex_model / codex_reasoning_effort
# - gemini -> gemini_model
# reconciler_model = "claude-opus-4-1"
# reconciler_reasoning_effort = "high"    # claude: low|medium|high|max, codex: low|medium|high

# Codex backend
codex_model = "gpt-5.3-codex"
codex_reasoning_effort = "low"   # default for local dev/test
# # low|medium|high

# Trigger mode
trigger_mode = "rerequest_only"  # rerequest_only|rerequest_or_commit
```

Or override backend from CLI:

```bash
uv run pr-reviewer run-once --enabled-reviewer codex --codex-backend cli
uv run pr-reviewer run-once --enabled-reviewer codex --codex-backend agents_sdk
uv run pr-reviewer run-once --codex-model gpt-5.3-codex --codex-reasoning-effort high
uv run pr-reviewer run-once --claude-model claude-sonnet-4-5 --claude-reasoning-effort medium
uv run pr-reviewer run-once --reconciler-backend codex --reconciler-model gpt-5.3-codex
uv run pr-reviewer run-once --reconciler-backend gemini --reconciler-model gemini-3.1-pro-preview
uv run pr-reviewer run-once --reconciler-model claude-opus-4-1 --reconciler-reasoning-effort high
uv run pr-reviewer run-once --pr-url https://github.com/<org>/<repo>/pull/<number> --auto-post-review
uv run pr-reviewer run-once --pr-url https://github.com/<org>/<repo>/pull/<number> --use-saved-review --auto-post-review
uv run pr-reviewer run-once --no-auto-post-review
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
uv run pr-reviewer run-once --pr-url https://github.com/<org>/<repo>/pull/<number>
```

## Behavior

- Polls open PRs in all configured owners (`github_orgs`) where `review-requested:@me`
- Excludes repos listed in `excluded_repos`
- Runs only reviewers listed in `enabled_reviewers`
- Uses selected Codex backend from `codex_backend`
- Uses selected reconciliation backend from `reconciler_backend`
- Uses trigger state machine from `trigger_mode`
- Codex CLI backend uses `codex review` and, when supported by CLI version, can parse JSON event output
- Skips draft PRs and (by default) PRs authored by you
- Skips PRs with fewer than 10 total changed lines (`additions + deletions`)
- Skips PRs that only touch config-like files (`.yaml`, `.yml`, `.json`, `.toml`, etc.)
- Bootstraps all discovered candidate PRs when no prior state exists
- After bootstrap, processes PRs when a newer direct re-request to you is observed
- Runs all enabled reviewers in parallel
- Injects PR issue-thread comments into reconciliation context (multi-reviewer mode)
- Reconciles with selected backend (Claude/Codex/Gemini) and writes:
  `reviews/<org>/<repo>/pr-<number>.md`
- Also writes versioned historical reviews under:
  `reviews/<org>/<repo>/pr-<number>/<timestamp>-<shortsha>.md`
- Saves raw reviewer outputs to:
  `reviews/<org>/<repo>/pr-<number>.raw.md`
- Also writes versioned raw outputs under:
  `reviews/<org>/<repo>/pr-<number>/<timestamp>-<shortsha>.raw.md`
- Prints file path when ready
- Optional comment posting when `auto_post_review = true`
- Optional formal review submission when `auto_submit_review_decision = true`
- `run-once --pr-url ...` reviews only specific PR URL(s)
- `run-once --pr-url ... --use-saved-review` reuses existing `pr-<number>.md` and continues to posting/submission without regenerating

## Lint and test

```bash
uv run ruff check .
uv run ruff format .
uv run pytest
```
