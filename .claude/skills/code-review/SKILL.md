---
name: code-review
description: Review local code changes using the code-reviewer CLI before committing or opening a PR. Use this skill whenever the user says "review my changes", "review this code", "check my work", "run a review", or after completing an implementation plan, large refactor, or feature. Also trigger when the user is about to commit or create a PR and hasn't reviewed yet — proactively suggest a review if significant changes were made. MANDATORY after multi-file changes, refactoring, or security-sensitive modifications. This skill runs a multi-model AI review pipeline (triage → lightweight or full review with reconciliation) against the local git diff.
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "if echo \"$TOOL_INPUT\" | grep -qE 'git commit'; then echo 'Before committing, have you reviewed these changes? Use the code-review skill to run a multi-model review: code-reviewer review --uncommitted'; fi"
---

Multi-model AI review pipeline that runs locally against git diffs. Every change goes through triage first — simple
changes get a fast lightweight review, complex changes get parallel multi-reviewer analysis with reconciliation.


## Modes of Operation

### Mode 1: Auto-Trigger (Post-Implementation)

**Triggers after:**
- Completing an implementation plan or large feature
- Multi-file refactors or architecture changes
- Security-sensitive modifications
- Before committing or creating a PR

### Mode 2: Slash Command

```
/code-review                      # Auto-detect: uncommitted or branch diff
/code-review --uncommitted        # Review uncommitted changes only
/code-review --base main          # Review current branch vs main
/code-review --commit abc123      # Review a specific commit
```

## Prerequisites

Before running, verify the environment is ready. If anything fails, tell the user and stop.

```bash
# 1. Check if code-reviewer CLI is installed
if ! command -v code-reviewer &>/dev/null; then
  echo "ERROR: code-reviewer CLI not installed."
  echo "Install with: pip install git+https://github.com/Inkvi/code-reviewer.git"
  exit 1
fi

# 2. Verify we're in a git repository
git rev-parse --git-dir &>/dev/null || {
  echo "ERROR: Not a git repository."
  exit 1
}
```

**Config is optional.** The CLI tries `./config.toml` first, then falls back to built-in defaults if missing. API keys depend on which backends are enabled:
- Claude: `ANTHROPIC_API_KEY`
- Codex: `OPENAI_API_KEY`
- Gemini: `gemini` CLI authenticated

## Detecting Review Mode

The skill must choose the right mode based on git state. The user can also override explicitly.

```bash
# Check for uncommitted changes (staged + unstaged + untracked)
DIRTY=$(git status --porcelain 2>/dev/null)

# Get current branch
CURRENT_BRANCH=$(git branch --show-current 2>/dev/null)

# Detect default branch
DEFAULT_BRANCH=$(git rev-parse --verify main 2>/dev/null && echo "main" || \
                 git rev-parse --verify master 2>/dev/null && echo "master" || \
                 echo "")
```

| Git State                         | Mode        | Flags                                     |
|-----------------------------------|-------------|-------------------------------------------|
| Uncommitted changes exist         | uncommitted | `--uncommitted`                           |
| Clean worktree, on feature branch | branch      | `--base <default-branch>`                 |
| User provides commit SHA          | commit      | `--commit <sha>`                          |
| Uncommitted + feature branch      | uncommitted | `--uncommitted` (mention `--base` option) |

**When both uncommitted changes and a branch diff exist:** prefer `--uncommitted` for immediate feedback. After
presenting results, mention: "You can also review the full branch diff with `--base <default-branch>` after committing."

**If the base branch is ambiguous** (no `main` or `master`, or the user might mean a different branch), use
`AskUserQuestion` to confirm.

## Running the Review

Launch the review as a **background task** using the Bash tool with `run_in_background: true`. This lets the user continue working while the review runs (typically 5-10 minutes). Tell the user the review is running in the background.

```bash
code-reviewer review \
  --repo . \
  <mode-flags> \
  --output-format json
```

When the background task completes, you will be notified automatically. Use `TaskOutput` to read the result, then parse and present it.

The `--output-format json` flag sends logs to stderr and structured results to stdout.

### JSON Output Schema

```json
{
  "processed": true,
  "status": "reviewed",
  "final_review": "## Code Review\n...",
  "error": null
}
```

| Field          | Type         | Description                               |
|----------------|--------------|-------------------------------------------|
| `processed`    | boolean      | Whether the review completed successfully |
| `status`       | string       | Outcome: `reviewed`, `skipped`, `error`   |
| `final_review` | string\|null | The full markdown review content          |
| `error`        | string\|null | Error message if failed                   |

## Presenting Results

### Successful Review

Parse `final_review` from the JSON output and present it to the user. The review is already formatted as markdown.
Present it directly — do not summarize or reformat.

If the review contains findings, organize follow-up by severity:

1. **Critical issues** — must fix before committing
2. **Suggestions** — worth considering
3. **Test gaps** — areas lacking test coverage

If there are **critical or high-severity** findings, evaluate each one by reading the relevant code to determine if the finding is valid or a false positive. Only fix the ones that are genuinely valid issues. After fixing, re-run the review. Repeat this validate-fix-review cycle until no valid critical/high issues remain.

For remaining low-severity suggestions, evaluate and fix the valid ones at your discretion.

### Failed Review

If `processed` is false, show the error and suggest troubleshooting:

```markdown
## Review Failed

**Error:** [error message]

**Troubleshooting:**

- Check API keys are set for configured backends
- If using a config file, verify settings are valid: `code-reviewer check`
- Check the stderr output above for detailed error info
```

## CLI Reference

| Flag                           | Description                                          |
|--------------------------------|------------------------------------------------------|
| `--config`, `-c`               | Path to TOML config file (optional, tries `./config.toml` then built-in defaults) |
| `--repo`                       | Path to git repository (default: `.`)                |
| `--base`                       | Base branch to diff against (branch mode)            |
| `--branch`                     | Head branch to review (default: current branch)      |
| `--uncommitted`                | Review staged + unstaged changes vs HEAD             |
| `--commit`                     | Review a specific commit vs its parent               |
| `--output-format`              | `text` (default) or `json`                           |
| `--enabled-reviewer`, `-r`     | Override reviewers (repeat for multiple)             |
| `--triage-backend`             | Override triage backend: `claude`, `codex`, `gemini` |
| `--lightweight-review-backend` | Override lightweight review backend                  |
