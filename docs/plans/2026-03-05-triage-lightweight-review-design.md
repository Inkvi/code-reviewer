# Triage-First Pipeline with Lightweight Review

## Problem

Config changes, Docker image bumps, and other simple PRs are currently skipped entirely. We want to give them a lightweight review instead, using a cheaper/faster model.

## Design

### New Pipeline Flow

```
PR candidate → trigger decision → triage → lightweight review OR full review → post
```

Replaces the current hard-skip logic (`_skip_reason_for_change_scope`) entirely. Every PR goes through triage — no more hard skips.

### Triage Step

New module: `reviewers/triage.py`

- Receives PR metadata + diff via existing CLI/SDK runners
- Classifies the PR as `"simple"` or `"full_review"` via JSON response
- A PR is "simple" if ALL of: no logic changes, no new business logic files, no security-sensitive changes
- On triage failure (timeout, API error), falls through to full review (safe default)
- Backend support: Claude, Codex, Gemini (same runner pattern as reconciler)
- Default timeout: 60 seconds

### Lightweight Review

New module: `reviewers/lightweight.py`

- Single model call with a checklist-oriented prompt
- Checklist items: valid syntax, hardcoded secrets, environment-specific values, breaking changes (removed keys, renamed fields, changed ports), version bump validity
- Output format: same `### Findings` + `### Test Gaps` as full pipeline
- Severity tags `[P1]`/`[P2]`/`[P3]` — approve/request-changes decision works the same way
- No reconciler. Single model, single pass.
- No mid-review commit detection (too fast to need it)
- Backend support: Claude, Codex, Gemini

### Configuration

```toml
# Triage
triage_backend = "gemini"               # claude | codex | gemini
triage_model = "gemini-3-flash"         # model for classification
triage_timeout_seconds = 60

# Lightweight review
lightweight_review_backend = "claude"            # claude | codex | gemini
lightweight_review_model = "claude-sonnet-4-6"
lightweight_review_reasoning_effort = "low"
lightweight_review_timeout_seconds = 300
```

All fields have CLI overrides following the existing `--flag-name` pattern.

### State Tracking

`last_status` gains new values: `lightweight_posted`, `lightweight_approved`, `lightweight_changes_requested`, `lightweight_generated`.

### Integration into `process_candidate`

1. Handle slash command / trigger decision (unchanged)
2. Prepare workspace (unchanged)
3. Run triage → returns `"simple"` or `"full_review"`
4. If `"simple"`: run lightweight review → validate format → write output → publish
5. If `"full_review"`: existing multi-reviewer + reconciler pipeline (unchanged)

Token usage logged as `[triage]` and `[lightweight]`.

## Files Changed

| File | Change |
|---|---|
| `config.py` | Add 6 new fields + validators |
| `processor.py` | Remove skip logic, insert triage call, route to lightweight or full pipeline |
| `reviewers/triage.py` | New — triage prompt + JSON parsing |
| `reviewers/lightweight.py` | New — checklist prompt |
| `reviewers/__init__.py` | Export new functions |
| `cli.py` | CLI overrides + check table rows |
| `config.example.toml` | Document new options |
| Tests | Update skip-related tests, add triage + lightweight tests |

## What's NOT Changing

- Full review pipeline (reviewers, reconciler, decision logic)
- GitHub integration, state machine, slash commands, workspace management
- Output format, posting logic, state persistence

## Constraints

- All model calls go through existing CLI/SDK runners (Claude SDK, Codex CLI/agents SDK, Gemini CLI)
- No direct API calls
