# Decouple Discovery from Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple the daemon's PR discovery loop from review processing so new PRs are discovered on a fixed interval regardless of in-flight reviews.

**Architecture:** Refactor `start_daemon` into a producer-consumer model. A discovery coroutine polls GitHub every `poll_interval_seconds` and enqueues candidates onto an `asyncio.Queue`. A pool of `max_parallel_prs` worker coroutines pull from the queue and process reviews. A shared `scheduled: set[str]` prevents duplicate enqueuing.

**Tech Stack:** Python 3.12+, asyncio, pytest

**Spec:** `docs/superpowers/specs/2026-03-20-decouple-discovery-from-processing-design.md`

---

### File Map

- **Modify:** `src/code_reviewer/daemon.py` — extract `_discover_candidates`, add `_discovery_loop`, `_worker`, refactor `start_daemon`
- **Modify:** `tests/test_daemon.py` — update existing tests, add tests for discovery loop, worker, and integration

---

### Task 1: Extract `_discover_candidates` helper

Extract the discovery logic (lines 30-62 of `daemon.py`) into a reusable function that both `run_cycle` and the new `_discovery_loop` can call.

**Files:**
- Modify: `src/code_reviewer/daemon.py:23-62`
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Write test for `_discover_candidates`**

```python
def test_discover_candidates_merges_slash_commands(monkeypatch, tmp_path) -> None:
    """_discover_candidates returns merged list of regular + slash command PRs."""
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=True)
    preflight = PreflightResult(viewer_login="inkvi")
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store._owns_lock = True
    store.load()

    regular_pr = _sample_pr(1)
    slash_pr = _sample_pr(2)
    slash_pr.slash_command_trigger = SlashCommandTrigger(
        comment_id=100, comment_author="bob", comment_created_at="2026-03-05T10:00:00Z"
    )

    monkeypatch.setattr(
        GitHubClient, "discover_pr_candidates", lambda _self, _config: [regular_pr]
    )
    monkeypatch.setattr(
        GitHubClient,
        "discover_slash_command_candidates",
        lambda _self, _config, _store: [slash_pr],
    )

    from code_reviewer.daemon import _discover_candidates

    client = GitHubClient(viewer_login=preflight.viewer_login)
    candidates = asyncio.run(_discover_candidates(config, client, store))
    keys = [c.key for c in candidates]
    assert "polymerdao/bridge-master#1" in keys
    assert "polymerdao/bridge-master#2" in keys
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_discover_candidates_merges_slash_commands -v`
Expected: FAIL — `_discover_candidates` does not exist yet

- [ ] **Step 3: Extract `_discover_candidates` from `run_cycle`**

In `daemon.py`, add this function before `run_cycle`:

```python
async def _discover_candidates(
    config: AppConfig,
    client: GitHubClient,
    store: StateStore,
) -> list[PRCandidate]:
    """Discover PR candidates from GitHub (regular + slash command)."""
    try:
        candidates = await asyncio.to_thread(client.discover_pr_candidates, config)
    except Exception as exc:  # noqa: BLE001
        warn(f"Failed to discover PRs: {exc}")
        return []

    if config.slash_command_enabled:
        try:
            slash_candidates = await asyncio.to_thread(
                client.discover_slash_command_candidates, config, store
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"Failed to discover slash command PRs: {exc}")
            slash_candidates = []

        existing_keys = {pr.key.lower() for pr in candidates}
        for sc in slash_candidates:
            if sc.key.lower() not in existing_keys:
                candidates.append(sc)
            else:
                candidates = [
                    sc if c.key.lower() == sc.key.lower() else c for c in candidates
                ]

    return candidates
```

Then update `run_cycle` to call it:

```python
async def run_cycle(
    config: AppConfig,
    preflight: PreflightResult,
    store: StateStore,
    *,
    verbose: bool = True,
) -> int:
    client = GitHubClient(viewer_login=preflight.viewer_login)
    workspace_mgr = PRWorkspace(Path(config.clone_root))

    candidates = await _discover_candidates(config, client, store)

    if not candidates:
        if verbose:
            info("No candidate PRs found")
        return 0

    # ... rest unchanged from line 61 onward ...
```

- [ ] **Step 4: Run all tests to verify nothing broke**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: All tests pass (existing + new)

- [ ] **Step 5: Commit**

```bash
git add src/code_reviewer/daemon.py tests/test_daemon.py
git commit -m "refactor: extract _discover_candidates helper from run_cycle"
```

---

### Task 2: Implement `_discovery_loop`

The discovery coroutine that polls on a fixed interval and enqueues new candidates.

**Files:**
- Modify: `src/code_reviewer/daemon.py`
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Write test for `_discovery_loop`**

```python
def test_discovery_loop_enqueues_candidates(monkeypatch) -> None:
    """Discovery loop enqueues new candidates and skips already-scheduled ones."""
    config = AppConfig(github_orgs=["polymerdao"], poll_interval_seconds=15)
    preflight = PreflightResult(viewer_login="inkvi")

    pr1 = _sample_pr(1)
    pr2 = _sample_pr(2)

    monkeypatch.setattr(
        "code_reviewer.daemon._discover_candidates",
        lambda _config, _client, _store: asyncio.coroutine(lambda: [pr1, pr2])(),
    )
    monkeypatch.setattr("code_reviewer.daemon.refresh_github_token", lambda: None)

    queue: asyncio.Queue[PRCandidate | None] = asyncio.Queue()
    scheduled: set[str] = set()
    # Pre-schedule pr1 to verify it gets skipped
    scheduled.add(pr1.key)
    shutdown = asyncio.Event()

    from code_reviewer.daemon import _discovery_loop

    async def run() -> None:
        # Stop after one iteration
        async def fake_discover(_config, _client, _store):
            return [pr1, pr2]

        monkeypatch.setattr("code_reviewer.daemon._discover_candidates", fake_discover)

        # Schedule shutdown after a short delay so we get one iteration
        async def stop_soon():
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(stop_soon())
        await _discovery_loop(config, preflight, object(), queue, scheduled, shutdown)

    asyncio.run(run())

    # Only pr2 should be enqueued (pr1 was already scheduled)
    assert queue.qsize() == 1
    item = queue.get_nowait()
    assert item.key == pr2.key
    assert pr2.key in scheduled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_discovery_loop_enqueues_candidates -v`
Expected: FAIL — `_discovery_loop` does not exist yet

- [ ] **Step 3: Implement `_discovery_loop`**

Add to `daemon.py` after `_discover_candidates`:

```python
async def _discovery_loop(
    config: AppConfig,
    preflight: PreflightResult,
    store: StateStore,
    queue: asyncio.Queue[PRCandidate | None],
    scheduled: set[str],
    shutdown: asyncio.Event,
    *,
    reload_config: Callable[[], AppConfig] | None = None,
) -> None:
    """Poll GitHub for PR candidates and enqueue new ones."""
    while not shutdown.is_set():
        if reload_config is not None:
            try:
                config = reload_config()
            except Exception as exc:  # noqa: BLE001
                warn(f"Config reload failed, using previous config: {exc}")
        try:
            await asyncio.to_thread(refresh_github_token)
            client = GitHubClient(viewer_login=preflight.viewer_login)
            candidates = await _discover_candidates(config, client, store)
            queued = 0
            skipped = 0
            for pr in candidates:
                if pr.key in scheduled:
                    skipped += 1
                else:
                    scheduled.add(pr.key)
                    queue.put_nowait(pr)
                    queued += 1
            if candidates:
                info(
                    f"Discovery: found {len(candidates)}, "
                    f"queued {queued}, skipped {skipped} already scheduled"
                )
        except Exception as exc:  # noqa: BLE001
            warn(f"Discovery cycle failed: {exc}")
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=config.poll_interval_seconds)
        except TimeoutError:
            pass
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/code_reviewer/daemon.py tests/test_daemon.py
git commit -m "feat: add _discovery_loop for continuous PR polling"
```

---

### Task 3: Implement `_worker`

The worker coroutine that pulls PRs from the queue and processes them.

**Files:**
- Modify: `src/code_reviewer/daemon.py`
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Write test for `_worker`**

```python
def test_worker_processes_and_clears_scheduled(monkeypatch) -> None:
    """Worker processes a PR from the queue and removes its key from scheduled."""
    config = AppConfig(github_orgs=["polymerdao"])
    preflight = PreflightResult(viewer_login="inkvi")
    pr = _sample_pr(7)

    processed_keys: list[str] = []

    async def fake_process(_config, _client, _store, _workspace, candidate, **_kw):
        processed_keys.append(candidate.key)
        return ProcessingResult(
            processed=True, pr_url=candidate.url, pr_key=candidate.key, status="generated"
        )

    monkeypatch.setattr("code_reviewer.daemon.process_candidate", fake_process)

    queue: asyncio.Queue[PRCandidate | None] = asyncio.Queue()
    scheduled: set[str] = {pr.key}
    queue.put_nowait(pr)
    queue.put_nowait(None)  # sentinel to stop the worker

    from code_reviewer.daemon import _worker

    asyncio.run(_worker(config, preflight, object(), queue, scheduled))

    assert processed_keys == [pr.key]
    assert pr.key not in scheduled


def test_worker_clears_scheduled_on_exception(monkeypatch) -> None:
    """Worker removes key from scheduled even when process_candidate raises."""
    config = AppConfig(github_orgs=["polymerdao"])
    preflight = PreflightResult(viewer_login="inkvi")
    pr = _sample_pr(8)

    async def exploding_process(_config, _client, _store, _workspace, _pr, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("code_reviewer.daemon.process_candidate", exploding_process)
    monkeypatch.setattr("code_reviewer.daemon.warn", lambda _msg: None)

    queue: asyncio.Queue[PRCandidate | None] = asyncio.Queue()
    scheduled: set[str] = {pr.key}
    queue.put_nowait(pr)
    queue.put_nowait(None)  # sentinel

    from code_reviewer.daemon import _worker

    asyncio.run(_worker(config, preflight, object(), queue, scheduled))

    assert pr.key not in scheduled
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_daemon.py::test_worker_processes_and_clears_scheduled tests/test_daemon.py::test_worker_clears_scheduled_on_exception -v`
Expected: FAIL — `_worker` does not exist yet

- [ ] **Step 3: Implement `_worker`**

Add to `daemon.py`:

```python
async def _worker(
    config: AppConfig,
    preflight: PreflightResult,
    store: StateStore,
    queue: asyncio.Queue[PRCandidate | None],
    scheduled: set[str],
) -> None:
    """Pull PRs from the queue and process them."""
    client = GitHubClient(viewer_login=preflight.viewer_login)
    workspace_mgr = PRWorkspace(Path(config.clone_root))

    while True:
        candidate = await queue.get()
        if candidate is None:
            queue.task_done()
            break
        try:
            result = await process_candidate(
                config, client, store, workspace_mgr, candidate, verbose=False
            )
            info(f"Worker done: {candidate.key} status={result.status}")
        except Exception as exc:  # noqa: BLE001
            warn(f"Worker failed for {candidate.key}: {exc}")
        finally:
            scheduled.discard(candidate.key)
            queue.task_done()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/code_reviewer/daemon.py tests/test_daemon.py
git commit -m "feat: add _worker coroutine for queue-based PR processing"
```

---

### Task 4: Refactor `start_daemon` to use producer-consumer

Wire `_discovery_loop` and `_worker` together in `start_daemon`.

**Files:**
- Modify: `src/code_reviewer/daemon.py:116-156`
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Write integration test**

```python
def test_start_daemon_producer_consumer(monkeypatch) -> None:
    """start_daemon discovers PRs and processes them via workers."""
    config = AppConfig(github_orgs=["polymerdao"], poll_interval_seconds=15, max_parallel_prs=2)
    preflight = PreflightResult(viewer_login="inkvi")

    pr = _sample_pr(20)
    discovery_count = 0
    processed_keys: list[str] = []

    async def fake_discover(_config, _client, _store):
        nonlocal discovery_count
        discovery_count += 1
        # Only return the PR on first discovery
        if discovery_count == 1:
            return [pr]
        return []

    async def fake_process(_config, _client, _store, _workspace, candidate, **_kw):
        processed_keys.append(candidate.key)
        return ProcessingResult(
            processed=True, pr_url=candidate.url, pr_key=candidate.key, status="generated"
        )

    monkeypatch.setattr("code_reviewer.daemon._discover_candidates", fake_discover)
    monkeypatch.setattr("code_reviewer.daemon.process_candidate", fake_process)
    monkeypatch.setattr("code_reviewer.daemon.refresh_github_token", lambda: None)
    monkeypatch.setattr("code_reviewer.daemon.is_github_app_auth", lambda: False)

    async def run() -> None:
        shutdown = asyncio.Event()

        async def stop_after_processing():
            # Give enough time for discovery + worker to process
            for _ in range(50):
                await asyncio.sleep(0.02)
                if processed_keys:
                    break
            shutdown.set()

        asyncio.create_task(stop_after_processing())
        await start_daemon(config, preflight, object(), shutdown_event=shutdown)

    asyncio.run(run())

    assert pr.key in processed_keys
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_start_daemon_producer_consumer -v`
Expected: FAIL — `start_daemon` still uses the old sequential loop

- [ ] **Step 3: Refactor `start_daemon`**

Replace the body of `start_daemon` (lines 124-156):

```python
async def start_daemon(
    config: AppConfig,
    preflight: PreflightResult,
    store: StateStore,
    *,
    reload_config: Callable[[], AppConfig] | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    info(
        "Starting daemon with "
        f"interval={config.poll_interval_seconds}s owners={','.join(config.github_owners)} "
        f"workers={config.max_parallel_prs}"
    )
    if is_github_app_auth():
        info("GitHub App auth detected — tokens will refresh each cycle")

    shutdown = shutdown_event or asyncio.Event()
    if shutdown_event is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown.set)

    queue: asyncio.Queue[PRCandidate | None] = asyncio.Queue()
    scheduled: set[str] = set()

    workers = [
        asyncio.create_task(_worker(config, preflight, store, queue, scheduled))
        for _ in range(config.max_parallel_prs)
    ]

    await _discovery_loop(
        config, preflight, store, queue, scheduled, shutdown, reload_config=reload_config
    )

    # Discovery stopped — send sentinels to drain workers
    for _ in workers:
        queue.put_nowait(None)
    await asyncio.gather(*workers)

    info("Shutting down daemon")
```

- [ ] **Step 4: Update `test_start_daemon_uses_quiet_run_cycle`**

This test tested the old sequential `start_daemon` calling `run_cycle`. It needs to be replaced with a test that verifies the new producer-consumer behavior. The integration test from step 1 already covers this. Remove the old test:

```python
# DELETE: test_start_daemon_uses_quiet_run_cycle — replaced by test_start_daemon_producer_consumer
```

- [ ] **Step 5: Run all tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: All pass

- [ ] **Step 6: Run linter**

Run: `uv run ruff check src/code_reviewer/daemon.py tests/test_daemon.py`
Expected: Clean (or only pre-existing E501)

- [ ] **Step 7: Commit**

```bash
git add src/code_reviewer/daemon.py tests/test_daemon.py
git commit -m "feat: refactor start_daemon to producer-consumer architecture

Discovery loop polls GitHub on a fixed interval independent of
in-flight reviews. Worker pool processes PRs from an asyncio.Queue.
Worst-case latency for new PR pickup drops from cycle-time + poll
interval to just the poll interval."
```

---

### Task 5: Run full test suite and lint

Final verification that nothing is broken across the entire project.

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

- [ ] **Step 2: Run linter**

Run: `uv run ruff check .`
Expected: Clean (or only pre-existing E501 violations)

- [ ] **Step 3: Run formatter check**

Run: `uv run ruff format --check .`
Expected: Clean
