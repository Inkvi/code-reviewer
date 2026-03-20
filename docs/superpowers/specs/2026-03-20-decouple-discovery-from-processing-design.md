# Decouple Discovery from Processing in Daemon

## Problem

The daemon's polling loop is sequential: `run_cycle` discovers PRs **and** processes all of them before returning. While reviews are in-flight (up to 15 min with 900s timeouts and 3 parallel PRs), no new PRs are discovered. Combined with the poll interval, worst-case latency from PR creation to review pickup can reach 7+ minutes.

## Solution

Refactor `start_daemon` into a producer-consumer architecture using `asyncio.Queue`:

- **Discovery loop** polls GitHub on a fixed interval, independent of review processing
- **Worker pool** (`max_parallel_prs` workers) pulls PRs from the queue and reviews them
- An `in_flight: set[str]` of PR keys prevents re-queuing PRs already being reviewed

## Architecture

```
┌─────────────────┐         ┌──────────────┐
│  discovery_loop  │──queue──▶  worker_pool  │ (max_parallel_prs workers)
│  (every 30s)     │         │              │
└─────────────────┘         └──────────────┘
        │                          │
        └──── in_flight: set[str] ─┘
```

## Discovery Loop

Runs every `poll_interval_seconds`:

1. Optionally reload config
2. Refresh GitHub token via `refresh_github_token()`
3. Call `discover_pr_candidates` + `discover_slash_command_candidates` (same merge logic as current `run_cycle`)
4. For each candidate: if `candidate.key` not in `in_flight`, enqueue it
5. Log summary: found N, queued M, skipped K in-flight
6. Wait `poll_interval_seconds` or shutdown

## Worker

N instances where N = `max_parallel_prs`:

1. Pull `PRCandidate` from queue (blocks until available or sentinel)
2. Add `candidate.key` to `in_flight`
3. Call `process_candidate` (unchanged signature and behavior)
4. Remove `candidate.key` from `in_flight`
5. Loop

## Shared State

- `asyncio.Queue[PRCandidate | None]` — unbounded. Backpressure is handled by the in-flight filter: if all workers are busy, new PRs for different keys queue up, but the same PR won't be re-queued.
- `in_flight: set[str]` — no lock needed. All access is from coroutines in the same asyncio event loop (single-threaded). The set is only mutated inside `await` boundaries, not from `to_thread` calls.

## Shutdown

1. Discovery loop checks `shutdown.is_set()` each iteration and exits
2. After discovery exits, push N `None` sentinels onto the queue (one per worker)
3. Workers exit when they receive `None`
4. `start_daemon` uses `asyncio.gather` to await discovery + all workers

## Decisions

- **Global concurrency cap**: `max_parallel_prs` remains the single cap on concurrent reviews. No separate queue depth limit.
- **Duplicate handling**: PRs already in `in_flight` are silently skipped. No re-evaluation until the current review finishes.
- **StateStore access**: Unchanged. `threading.Lock` inside `StateStore` is sufficient since `process_candidate` already uses `asyncio.to_thread` for state operations.

## What Changes

- `daemon.py`: `start_daemon` refactored into `_discovery_loop`, `_worker`, and orchestration in `start_daemon`. The current `run_cycle` function's discovery logic moves into `_discovery_loop`; its processing loop is replaced by the worker pool.
- `run_cycle` is preserved as a public function (used by `run-once` CLI command) but its internals may be refactored to share discovery logic with `_discovery_loop`.
- `test_daemon.py`: tests updated for the new structure.

## What Doesn't Change

- `process_candidate`, `StateStore`, `GitHubClient`, `PRWorkspace` — unchanged
- `create_daemon_app` — same lifespan pattern, calls refactored `start_daemon`
- Config schema — no new fields
- CLI — no changes

## Testing

- **Discovery loop**: mock `GitHubClient`, verify candidates are enqueued and in-flight keys are skipped
- **Worker**: put candidates directly on queue with mock `process_candidate`, verify processing and in-flight set management
- **Integration**: run both together with mocks, verify end-to-end flow
- **Existing tests**: `run_cycle` tests updated to test new discovery function
