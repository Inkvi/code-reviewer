# Slash Command Trigger for PR Reviews

## Problem

The current trigger mechanism requires manual reviewer assignment and re-request. This creates friction — PR authors must explicitly assign the bot as a reviewer and re-request each time they want a new review.

## Design

### Slash Command (`/review`)

During each poll cycle, after discovering reviewer-assigned PRs, the daemon also scans recent issue comments on PRs in monitored orgs.

**Command format:**
- `/review` — request a review (standalone line)
- `/review force` — force re-review even if current HEAD already reviewed

**Permission:** PR author or org members only. Verified via `gh api orgs/{org}/members/{login}`.

**Flow when `/review` is detected:**
1. React with eyes to the `/review` comment
2. Reply "Starting review of the latest changes..."
3. Check if current head SHA already has a posted review
   - If yes and no `force` flag: reply "Already reviewed at this commit. Push new changes or use `/review force` to re-review." and skip
   - If no or `force`: run the full review pipeline
4. Post review result

**Slash command candidates bypass the re-request trigger check** — the `/review` comment itself is the trigger.

### Comment Scanning

- For each monitored org, scan PRs with recent activity (comments in the last `poll_interval_seconds * 2` window)
- Parse comments looking for `/review` or `/review force` as a standalone line
- Only scan comments newer than the last poll timestamp stored in state
- Permission check: `gh api orgs/{org}/members/{login}` — allow if org member or PR author

### Discovery Changes

- Current: `gh search prs --review-requested @me`
- New: adds a second discovery path for slash commands
  - Poll recent issue comments across monitored orgs for `/review` commands
  - Use `gh search issues` to find PRs with recent comments, then check for unprocessed `/review`
  - Merge with existing reviewer-assigned candidates (deduplicate by PR key)
- State tracking: `last_processed_review_command_at` per PR to avoid reprocessing

### Processing Flow

```
Poll cycle
+-- Discover reviewer-assigned PRs (existing)
+-- Discover slash-command PRs (new)
|   +-- For each monitored org, find PRs with /review comments
+-- Merge & deduplicate candidates by PR key
+-- For each candidate:
    +-- If from slash command:
    |   +-- Check if current head SHA already reviewed
    |   |   +-- Yes + no "force" -> reply "already reviewed", skip
    |   |   +-- No or "force" -> proceed
    |   +-- React eyes to the /review comment
    |   +-- Reply "Starting review..."
    +-- If from reviewer assignment:
    |   +-- Existing trigger logic (re-request check, etc.)
    +-- Run review pipeline -> post result
```

### Config Changes

```toml
# Slash command trigger (default: true)
slash_command_enabled = true

# Auto-review all PRs — future, default off
auto_review_all_prs = false
```

CLI override: `--slash-command-enabled / --no-slash-command-enabled`

No changes to existing `trigger_mode` — slash command is an independent trigger path.

### Auto-Review All PRs (future, config-gated)

- `auto_review_all_prs = false` by default
- When enabled: any non-draft PR in monitored orgs gets reviewed on first detection, no reviewer assignment needed
- Slash command works regardless of this flag
- Not implemented in initial version
