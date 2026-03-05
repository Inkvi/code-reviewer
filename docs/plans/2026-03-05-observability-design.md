# Observability: Event Log, CLI Commands, and HTTP API

## Summary

Add operational visibility to pr-reviewer through three layers:

1. **Event log** — append-only JSONL file recording daemon activity
2. **State file extensions** — `_meta` key with aggregated operational metrics
3. **CLI commands** — `status`, `history`, `costs` for terminal inspection
4. **HTTP API** — `serve` subcommand exposing health checks and JSON endpoints

## Event Log

**File:** `.state/events.jsonl` (next to existing state file)

**Format:** One JSON object per line, append-only.

```jsonl
{"ts":"2026-03-05T10:00:00Z","event":"daemon_started","pid":12345}
{"ts":"2026-03-05T10:00:00Z","event":"cycle_start","cycle_id":1}
{"ts":"2026-03-05T10:00:02Z","event":"cycle_end","cycle_id":1,"candidates_found":3,"processed":1}
{"ts":"2026-03-05T10:00:03Z","event":"review_started","pr_key":"org/repo#42","head_sha":"abc123","triage_result":"full_review"}
{"ts":"2026-03-05T10:01:30Z","event":"reviewer_completed","pr_key":"org/repo#42","reviewer":"claude","status":"success","duration_s":87.2,"input_tokens":15000,"output_tokens":3200,"cost_usd":0.12}
{"ts":"2026-03-05T10:01:30Z","event":"reviewer_completed","pr_key":"org/repo#42","reviewer":"codex","status":"error","duration_s":45.0,"error":"timeout"}
{"ts":"2026-03-05T10:02:00Z","event":"review_completed","pr_key":"org/repo#42","status":"reviewed","duration_s":120.0,"total_cost_usd":0.12,"review_decision":"approve"}
{"ts":"2026-03-05T10:02:00Z","event":"review_failed","pr_key":"org/repo#42","error":"all reviewers failed"}
```

**Event types:**

- `daemon_started` — PID, config summary
- `cycle_start` / `cycle_end` — cycle ID, candidate count, processed count
- `review_started` — PR key, head SHA, triage result (simple/full_review)
- `reviewer_completed` — per-reviewer: status, duration, tokens, cost, error
- `review_completed` — final: status, total duration, total cost, decision
- `review_failed` — PR key, error message

**Implementation:** New `EventLog` class in `src/pr_reviewer/events.py`. Simple append with `open(path, "a")`. No locking needed (single writer — the daemon process already holds the PID lock).

**Rotation:** `max_event_log_bytes` config field (default 10MB). On each write, if file exceeds limit, rotate to `events.jsonl.1` (keep one backup).

## State File Extensions

Add a `_meta` key to the existing state JSON:

```json
{
  "_meta": {
    "daemon_started_at": "2026-03-05T10:00:00Z",
    "daemon_pid": 12345,
    "last_cycle_at": "2026-03-05T10:05:00Z",
    "total_cycles": 42,
    "total_prs_processed": 15,
    "total_prs_skipped": 27,
    "total_errors": 3,
    "cumulative_tokens": {
      "input": 450000,
      "output": 85000
    },
    "cumulative_cost_usd": 3.42,
    "per_reviewer_stats": {
      "claude": {"success": 12, "error": 1, "total_duration_s": 1440.0},
      "codex": {"success": 10, "error": 2, "total_duration_s": 980.0}
    }
  },
  "org/repo#42": { "...existing per-PR state..." }
}
```

- `_meta` is reserved — `StateStore.get()` already skips unknown keys, no PR key starts with `_`
- Updated atomically alongside PR state (same `save()` call)
- `StateStore` gets `get_meta()` / `update_meta()` methods
- `daemon_pid` + `daemon_started_at` let `status` command detect if the daemon is alive

## CLI Commands

All read-only (no lock needed — just read state file and event log).

### `pr-reviewer status`

```
Daemon:     running (pid 12345, up 2h 15m)
Last cycle: 30s ago (found 3 candidates, processed 1)
Cycles:     42 total, 3 errors

PRs:        15 processed, 27 skipped
Cost:       $3.42 (450K input / 85K output tokens)

Reviewers:
  claude    12 ok / 1 err    avg 120.0s
  codex     10 ok / 2 err    avg 98.0s
```

### `pr-reviewer history`

```
PR                      Status     Triage    Duration  Cost     When
org/repo#42             reviewed   full       2m 00s   $0.12   5m ago
org/repo#38             reviewed   simple       45s   $0.02   1h ago
org/repo#35             error      full         —      —      2h ago
other/lib#10            skipped    —            —      —      2h ago
```

Flags: `--since 24h` / `--since 7d`, `--pr org/repo#42`, `--output-format json`

Default: 50 most recent events.

### `pr-reviewer costs`

```
Period: last 24h (from event log)

By reviewer:
  claude     $2.80   320K in / 60K out    12 reviews
  codex      $0.50   100K in / 20K out    10 reviews
  gemini     $0.12    30K in /  5K out     3 reviews (triage)

By PR:
  org/repo#42     $0.12    2m 00s
  org/repo#38     $0.02      45s

Total: $3.42
```

Flags: `--since 24h` (default) / `--since 7d` / `--all`, `--output-format json`

All commands use Rich tables for terminal output.

## HTTP API

New subcommand: `pr-reviewer serve --port 9120`

Minimal HTTP server using stdlib `http.server`. Reads state file + event log on each request.

| Method | Path | Response | Purpose |
|--------|------|----------|---------|
| GET | `/health` | `{"status":"ok","daemon_pid":12345,"daemon_alive":true,"last_cycle_at":"...","uptime_s":8100}` | Health checks |
| GET | `/api/status` | Same as `pr-reviewer status` in JSON | Tooling |
| GET | `/api/history?since=24h&pr=org/repo%2342` | Same as `pr-reviewer history` in JSON | Tooling |
| GET | `/api/costs?since=24h` | Same as `pr-reviewer costs` in JSON | Tooling |

- Read-only, no auth needed for local use
- Binds to `127.0.0.1` by default; `--host 0.0.0.0` to expose
- Runs independently of the daemon (separate process, no lock)
- `/health` returns `200` if daemon PID alive, `503` if not

## Integration Points

| Location | Event | Captured |
|----------|-------|----------|
| `daemon.py:start_daemon` | `daemon_started` | PID, config summary |
| `daemon.py:run_cycle` top | `cycle_start` | cycle_id |
| `daemon.py:run_cycle` bottom | `cycle_end` | candidates_found, processed |
| `processor.py` after triage | `review_started` | pr_key, head_sha, triage_result |
| `processor.py` after each reviewer | `reviewer_completed` | reviewer, status, duration, tokens, cost, error |
| `processor.py` after final output | `review_completed` | pr_key, status, duration, cost, decision |
| `processor.py` on failure | `review_failed` | pr_key, error |

## File Changes

**New files:**

- `src/pr_reviewer/events.py` — `EventLog` class
- `src/pr_reviewer/server.py` — HTTP API server
- `tests/test_events.py`
- `tests/test_server.py`

**Modified files:**

- `models.py` — add `DaemonMeta` dataclass
- `state.py` — add `get_meta()` / `update_meta()`
- `config.py` — add `max_event_log_bytes` field
- `cli.py` — add `status`, `history`, `costs`, `serve` commands
- `daemon.py` — emit cycle/daemon events, pass `EventLog`
- `processor.py` — emit review/reviewer events, update `_meta`
