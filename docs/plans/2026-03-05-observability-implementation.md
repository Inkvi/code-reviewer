# Observability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add operational visibility via event log, state metadata, CLI commands (status/history/costs), and a minimal HTTP API.

**Architecture:** Append-only JSONL event log alongside existing state file. State file gets a `_meta` key for aggregated metrics. New CLI commands read both. Minimal stdlib HTTP server exposes the same data as JSON.

**Tech Stack:** Python stdlib (json, http.server), Rich tables, existing Pydantic/Typer stack. No new dependencies.

---

### Task 1: DaemonMeta dataclass

**Files:**
- Modify: `src/pr_reviewer/models.py:142-157`
- Test: `tests/test_models_meta.py`

**Step 1: Write the failing test**

Create `tests/test_models_meta.py`:

```python
from pr_reviewer.models import DaemonMeta


def test_daemon_meta_defaults():
    meta = DaemonMeta()
    assert meta.daemon_started_at is None
    assert meta.daemon_pid is None
    assert meta.last_cycle_at is None
    assert meta.total_cycles == 0
    assert meta.total_prs_processed == 0
    assert meta.total_prs_skipped == 0
    assert meta.total_errors == 0
    assert meta.cumulative_input_tokens == 0
    assert meta.cumulative_output_tokens == 0
    assert meta.cumulative_cost_usd == 0.0
    assert meta.per_reviewer_stats == {}


def test_daemon_meta_to_dict_round_trip():
    meta = DaemonMeta(
        daemon_started_at="2026-03-05T10:00:00Z",
        daemon_pid=12345,
        total_cycles=5,
        total_prs_processed=3,
        cumulative_cost_usd=1.50,
        per_reviewer_stats={
            "claude": {"success": 2, "error": 1, "total_duration_s": 300.0},
        },
    )
    d = meta.to_dict()
    assert d["daemon_pid"] == 12345
    assert d["total_cycles"] == 5
    assert d["per_reviewer_stats"]["claude"]["success"] == 2

    restored = DaemonMeta.from_dict(d)
    assert restored.daemon_pid == 12345
    assert restored.total_cycles == 5
    assert restored.per_reviewer_stats["claude"]["success"] == 2


def test_daemon_meta_from_dict_handles_empty():
    meta = DaemonMeta.from_dict({})
    assert meta.total_cycles == 0
    assert meta.per_reviewer_stats == {}
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models_meta.py -v`
Expected: FAIL — `ImportError: cannot import name 'DaemonMeta'`

**Step 3: Write minimal implementation**

Add to `src/pr_reviewer/models.py` after the `ProcessedState` class (after line 157):

```python
@dataclass(slots=True)
class DaemonMeta:
    daemon_started_at: str | None = None
    daemon_pid: int | None = None
    last_cycle_at: str | None = None
    total_cycles: int = 0
    total_prs_processed: int = 0
    total_prs_skipped: int = 0
    total_errors: int = 0
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    cumulative_cost_usd: float = 0.0
    per_reviewer_stats: dict[str, dict[str, int | float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "daemon_started_at": self.daemon_started_at,
            "daemon_pid": self.daemon_pid,
            "last_cycle_at": self.last_cycle_at,
            "total_cycles": self.total_cycles,
            "total_prs_processed": self.total_prs_processed,
            "total_prs_skipped": self.total_prs_skipped,
            "total_errors": self.total_errors,
            "cumulative_tokens": {
                "input": self.cumulative_input_tokens,
                "output": self.cumulative_output_tokens,
            },
            "cumulative_cost_usd": self.cumulative_cost_usd,
            "per_reviewer_stats": self.per_reviewer_stats,
        }

    @staticmethod
    def from_dict(d: dict) -> DaemonMeta:
        tokens = d.get("cumulative_tokens", {})
        return DaemonMeta(
            daemon_started_at=d.get("daemon_started_at"),
            daemon_pid=d.get("daemon_pid"),
            last_cycle_at=d.get("last_cycle_at"),
            total_cycles=d.get("total_cycles", 0),
            total_prs_processed=d.get("total_prs_processed", 0),
            total_prs_skipped=d.get("total_prs_skipped", 0),
            total_errors=d.get("total_errors", 0),
            cumulative_input_tokens=tokens.get("input", 0),
            cumulative_output_tokens=tokens.get("output", 0),
            cumulative_cost_usd=d.get("cumulative_cost_usd", 0.0),
            per_reviewer_stats=d.get("per_reviewer_stats", {}),
        )
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models_meta.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_models_meta.py src/pr_reviewer/models.py
git commit -m "feat: add DaemonMeta dataclass for operational metrics"
```

---

### Task 2: EventLog class

**Files:**
- Create: `src/pr_reviewer/events.py`
- Test: `tests/test_events.py`

**Step 1: Write the failing test**

Create `tests/test_events.py`:

```python
import json
from pathlib import Path

from pr_reviewer.events import EventLog


def test_event_log_append_creates_file(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    log.emit("daemon_started", pid=12345)

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "daemon_started"
    assert event["pid"] == 12345
    assert "ts" in event


def test_event_log_appends_multiple(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    log.emit("cycle_start", cycle_id=1)
    log.emit("cycle_end", cycle_id=1, processed=2)

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "cycle_start"
    assert json.loads(lines[1])["processed"] == 2


def test_event_log_rotation(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path, max_bytes=200)

    # Write enough events to exceed 200 bytes
    for i in range(20):
        log.emit("cycle_start", cycle_id=i)

    assert log_path.exists()
    backup = tmp_path / "events.jsonl.1"
    assert backup.exists()
    # Current file should be smaller than max after rotation
    assert log_path.stat().st_size < 400


def test_event_log_read_events(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    log.emit("review_completed", pr_key="org/repo#1", status="reviewed")
    log.emit("review_failed", pr_key="org/repo#2", error="timeout")

    events = log.read_events()
    assert len(events) == 2
    assert events[0]["pr_key"] == "org/repo#1"
    assert events[1]["event"] == "review_failed"


def test_event_log_read_events_empty(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    events = log.read_events()
    assert events == []


def test_event_log_read_events_with_since(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    # Write events with known timestamps
    log_path.write_text(
        '{"ts":"2026-03-04T10:00:00Z","event":"old"}\n'
        '{"ts":"2026-03-05T10:00:00Z","event":"new"}\n',
        encoding="utf-8",
    )
    log = EventLog(log_path)
    events = log.read_events(since="2026-03-05T00:00:00Z")
    assert len(events) == 1
    assert events[0]["event"] == "new"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pr_reviewer.events'`

**Step 3: Write minimal implementation**

Create `src/pr_reviewer/events.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class EventLog:
    def __init__(self, path: Path, max_bytes: int = 10 * 1024 * 1024) -> None:
        self.path = path
        self.max_bytes = max_bytes

    def emit(self, event: str, **data: object) -> None:
        entry = {
            "ts": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "event": event,
            **data,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        self._maybe_rotate()

    def _maybe_rotate(self) -> None:
        try:
            if self.path.stat().st_size <= self.max_bytes:
                return
        except OSError:
            return
        backup = self.path.with_suffix(self.path.suffix + ".1")
        try:
            self.path.replace(backup)
        except OSError:
            pass

    def read_events(self, since: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        events: list[dict] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since is not None and event.get("ts", "") < since:
                    continue
                events.append(event)
        return events
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_events.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/events.py tests/test_events.py
git commit -m "feat: add EventLog class for append-only JSONL event logging"
```

---

### Task 3: StateStore meta support

**Files:**
- Modify: `src/pr_reviewer/state.py:1-113`
- Test: `tests/test_state.py`

**Step 1: Write the failing test**

Append to `tests/test_state.py`:

```python
from pr_reviewer.models import DaemonMeta


def test_state_store_get_meta_empty(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store.load()
    meta = store.get_meta()
    assert meta.total_cycles == 0
    assert meta.daemon_pid is None


def test_state_store_update_meta_round_trip(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store.load()

    meta = store.get_meta()
    meta.daemon_pid = 12345
    meta.total_cycles = 10
    meta.cumulative_cost_usd = 2.50
    meta.per_reviewer_stats = {"claude": {"success": 5, "error": 1, "total_duration_s": 600.0}}
    store.update_meta(meta)
    store.save()

    store2 = StateStore(state_path)
    store2.load()
    meta2 = store2.get_meta()
    assert meta2.daemon_pid == 12345
    assert meta2.total_cycles == 10
    assert meta2.cumulative_cost_usd == 2.50
    assert meta2.per_reviewer_stats["claude"]["success"] == 5


def test_state_store_meta_does_not_interfere_with_pr_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store.load()

    store.set(
        "org/repo#1",
        ProcessedState(last_reviewed_head_sha="abc", last_status="generated"),
    )
    meta = store.get_meta()
    meta.total_cycles = 5
    store.update_meta(meta)
    store.save()

    store2 = StateStore(state_path)
    store2.load()
    assert store2.get("org/repo#1").last_reviewed_head_sha == "abc"
    assert store2.get_meta().total_cycles == 5


def test_state_store_get_ignores_meta_key(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store.load()

    meta = store.get_meta()
    meta.daemon_pid = 99
    store.update_meta(meta)
    store.save()

    store2 = StateStore(state_path)
    store2.load()
    # get("_meta") should return empty ProcessedState, not crash
    result = store2.get("_meta")
    assert result.last_reviewed_head_sha is None
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_state.py::test_state_store_get_meta_empty -v`
Expected: FAIL — `AttributeError: 'StateStore' object has no attribute 'get_meta'`

**Step 3: Write minimal implementation**

Modify `src/pr_reviewer/state.py`. Add import at top:

```python
from pr_reviewer.models import DaemonMeta, ProcessedState
```

Add two methods to the `StateStore` class (after the `set` method at line 112):

```python
    def get_meta(self) -> DaemonMeta:
        raw = self._data.get("_meta", {})
        return DaemonMeta.from_dict(raw)

    def update_meta(self, meta: DaemonMeta) -> None:
        self._data["_meta"] = meta.to_dict()
```

Modify the `get` method to handle the `_meta` key gracefully — no changes needed since `_meta` won't have the expected per-PR fields, and `item.get(...)` will return `None` for all, which is already the default behavior.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_state.py -v`
Expected: PASS (all existing + new tests)

**Step 5: Commit**

```bash
git add src/pr_reviewer/state.py tests/test_state.py
git commit -m "feat: add get_meta/update_meta to StateStore"
```

---

### Task 4: Config field for max_event_log_bytes

**Files:**
- Modify: `src/pr_reviewer/config.py:51`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Append to `tests/test_config.py` (find where config tests are):

```python
def test_max_event_log_bytes_default(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('github_orgs = ["test"]\n', encoding="utf-8")
    config = load_config(config_path)
    assert config.max_event_log_bytes == 10 * 1024 * 1024


def test_max_event_log_bytes_custom(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'github_orgs = ["test"]\nmax_event_log_bytes = 5000000\n',
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.max_event_log_bytes == 5_000_000
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_max_event_log_bytes_default -v`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'max_event_log_bytes'`

**Step 3: Write minimal implementation**

Add to `src/pr_reviewer/config.py` in the `AppConfig` class, after the lightweight review fields (after line 51):

```python
    # Observability
    max_event_log_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/config.py tests/test_config.py
git commit -m "feat: add max_event_log_bytes config field"
```

---

### Task 5: Wire events into daemon.py

**Files:**
- Modify: `src/pr_reviewer/daemon.py`
- Modify: `src/pr_reviewer/cli.py` (create EventLog in _load_runtime, pass to daemon)
- Test: `tests/test_daemon.py`

**Step 1: Write the failing test**

Add to `tests/test_daemon.py`:

```python
import json
from pathlib import Path

from pr_reviewer.events import EventLog


def test_run_cycle_emits_cycle_events(monkeypatch, tmp_path) -> None:
    config = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    preflight = PreflightResult(viewer_login="inkvi")
    event_log = EventLog(tmp_path / "events.jsonl")

    monkeypatch.setattr(
        "pr_reviewer.daemon.GitHubClient.discover_pr_candidates",
        lambda _self, _config: [],
    )

    processed = asyncio.run(
        run_cycle(config, preflight, object(), event_log=event_log, verbose=False)
    )

    assert processed == 0
    events = event_log.read_events()
    types = [e["event"] for e in events]
    assert "cycle_start" in types
    assert "cycle_end" in types


def test_start_daemon_emits_daemon_started(monkeypatch, tmp_path) -> None:
    config = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    preflight = PreflightResult(viewer_login="inkvi")
    event_log = EventLog(tmp_path / "events.jsonl")

    async def fake_run_cycle(_config, _preflight, _store, *, event_log=None, verbose=True):
        return 0

    async def fake_sleep(_seconds):
        raise RuntimeError("stop")

    monkeypatch.setattr("pr_reviewer.daemon.run_cycle", fake_run_cycle)
    monkeypatch.setattr("pr_reviewer.daemon.asyncio.sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop"):
        asyncio.run(start_daemon(config, preflight, object(), event_log=event_log))

    events = event_log.read_events()
    assert events[0]["event"] == "daemon_started"
    assert "pid" in events[0]
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_daemon.py::test_run_cycle_emits_cycle_events -v`
Expected: FAIL — `TypeError: run_cycle() got an unexpected keyword argument 'event_log'`

**Step 3: Write minimal implementation**

Modify `src/pr_reviewer/daemon.py`:

Add import:
```python
import os

from pr_reviewer.events import EventLog
```

Update `run_cycle` signature to accept `event_log: EventLog | None = None`. Add cycle event emission:

```python
async def run_cycle(
    config: AppConfig,
    preflight: PreflightResult,
    store: StateStore,
    *,
    event_log: EventLog | None = None,
    verbose: bool = True,
) -> int:
    client = GitHubClient(viewer_login=preflight.viewer_login)
    workspace_mgr = PRWorkspace(Path(config.clone_root))

    cycle_id = None
    if event_log is not None:
        import time
        cycle_id = int(time.time() * 1000)
        event_log.emit("cycle_start", cycle_id=cycle_id)

    processed = 0
    try:
        candidates = client.discover_pr_candidates(config)
    except Exception as exc:  # noqa: BLE001
        warn(f"Failed to discover PRs: {exc}")
        if event_log is not None:
            event_log.emit("cycle_end", cycle_id=cycle_id, candidates_found=0, processed=0, error=str(exc))
        return 0

    if config.slash_command_enabled:
        try:
            slash_candidates = client.discover_slash_command_candidates(config, store)
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

    if not candidates:
        if verbose:
            info("No candidate PRs found")
        if event_log is not None:
            event_log.emit("cycle_end", cycle_id=cycle_id, candidates_found=0, processed=0)
        return 0

    if verbose:
        info(f"Found {len(candidates)} candidate PR(s)")

    if config.max_parallel_prs == 1:
        for index, pr in enumerate(candidates, start=1):
            if verbose:
                info(f"PR {index}/{len(candidates)} {pr.url}")
            result = await process_candidate(
                config,
                client,
                store,
                workspace_mgr,
                pr,
                event_log=event_log,
                verbose=verbose,
            )
            if result.processed:
                processed += 1
        if event_log is not None:
            event_log.emit("cycle_end", cycle_id=cycle_id, candidates_found=len(candidates), processed=processed)
        return processed

    semaphore = asyncio.Semaphore(config.max_parallel_prs)

    async def _bounded_process(pr: PRCandidate) -> bool:
        async with semaphore:
            r = await process_candidate(
                config,
                client,
                store,
                workspace_mgr,
                pr,
                event_log=event_log,
                verbose=verbose,
            )
            return r.processed

    tasks = [asyncio.create_task(_bounded_process(pr)) for pr in candidates]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            warn(f"Parallel PR processing failed: {result}")
        elif result:
            processed += 1

    if event_log is not None:
        event_log.emit("cycle_end", cycle_id=cycle_id, candidates_found=len(candidates), processed=processed)
    return processed
```

Update `start_daemon` to accept and pass `event_log`:

```python
async def start_daemon(
    config: AppConfig,
    preflight: PreflightResult,
    store: StateStore,
    *,
    event_log: EventLog | None = None,
) -> None:
    if event_log is not None:
        event_log.emit("daemon_started", pid=os.getpid(), owners=config.github_owners)
    info(
        "Starting daemon with "
        f"interval={config.poll_interval_seconds}s owners={','.join(config.github_owners)}"
    )
    while True:
        try:
            processed = await run_cycle(config, preflight, store, event_log=event_log, verbose=False)
            info(f"Cycle complete. Processed {processed} PR(s)")
        except Exception as exc:  # noqa: BLE001
            warn(f"Cycle failed: {exc}")
        await asyncio.sleep(config.poll_interval_seconds)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: PASS (all existing + new tests — existing tests pass because event_log defaults to None)

**Step 5: Commit**

```bash
git add src/pr_reviewer/daemon.py tests/test_daemon.py
git commit -m "feat: emit cycle/daemon events from daemon.py"
```

---

### Task 6: Wire events into processor.py and update _meta

**Files:**
- Modify: `src/pr_reviewer/processor.py:461-470` (process_candidate signature + event emission)
- Test: `tests/test_processor_events.py`

**Step 1: Write the failing test**

Create `tests/test_processor_events.py`:

```python
import asyncio
from pathlib import Path

from pr_reviewer.config import AppConfig
from pr_reviewer.events import EventLog
from pr_reviewer.models import PRCandidate, ProcessedState, ReviewerOutput, TokenUsage
from pr_reviewer.state import StateStore


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="org",
        repo="repo",
        number=1,
        url="https://github.com/org/repo/pull/1",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="abc123",
        updated_at="2026-03-01T00:00:00Z",
        latest_direct_rerequest_at="2026-03-05T10:00:00Z",
        additions=10,
        deletions=5,
        changed_file_paths=["src/main.py"],
    )


def test_process_candidate_emits_review_events(monkeypatch, tmp_path: Path) -> None:
    from pr_reviewer.processor import process_candidate
    from pr_reviewer.reviewers.triage import TriageResult

    config = AppConfig(github_orgs=["org"], enabled_reviewers=["claude"])
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store._owns_lock = True
    store.load()
    event_log = EventLog(tmp_path / "events.jsonl")
    pr = _sample_pr()

    monkeypatch.setattr(
        "pr_reviewer.processor.run_triage",
        lambda *a, **kw: asyncio.coroutine(lambda: TriageResult.FULL_REVIEW)(),
    )

    from datetime import UTC, datetime

    fake_output = ReviewerOutput(
        reviewer="claude",
        status="ok",
        markdown="### Findings\n- None\n\n### Test Gaps\n- None noted.",
        stdout="",
        stderr="",
        error=None,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        token_usage=TokenUsage(input_tokens=1000, output_tokens=500, cost_usd=0.05),
    )

    async def fake_run_reviewers(_config, _client, _pr, _workdir):
        return {"claude": fake_output}

    monkeypatch.setattr(
        "pr_reviewer.processor._run_reviewers_with_monitoring", fake_run_reviewers,
    )
    monkeypatch.setattr(
        "pr_reviewer.processor.PRWorkspace.prepare", lambda _self, _pr: tmp_path / "work",
    )
    monkeypatch.setattr(
        "pr_reviewer.processor.PRWorkspace.cleanup", lambda _self, _path: None,
    )
    (tmp_path / "work").mkdir()

    class FakeClient:
        viewer_login = "bot"
        def add_eyes_reaction(self, _pr): pass
        def post_pr_comment_inline(self, _pr, _msg): pass

    result = asyncio.run(
        process_candidate(config, FakeClient(), store, object(), pr, event_log=event_log)
    )

    events = event_log.read_events()
    event_types = [e["event"] for e in events]
    assert "review_started" in event_types
    assert "reviewer_completed" in event_types
    assert "review_completed" in event_types

    # Check _meta was updated
    meta = store.get_meta()
    assert meta.total_prs_processed >= 1


def test_process_candidate_emits_review_failed_on_error(monkeypatch, tmp_path: Path) -> None:
    from pr_reviewer.processor import process_candidate

    config = AppConfig(github_orgs=["org"], enabled_reviewers=["claude"])
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store._owns_lock = True
    store.load()
    event_log = EventLog(tmp_path / "events.jsonl")
    pr = _sample_pr()

    monkeypatch.setattr(
        "pr_reviewer.processor.PRWorkspace.prepare",
        lambda _self, _pr: (_ for _ in ()).throw(RuntimeError("clone failed")),
    )
    monkeypatch.setattr(
        "pr_reviewer.processor.PRWorkspace.cleanup", lambda _self, _path: None,
    )

    class FakeClient:
        viewer_login = "bot"
        def add_eyes_reaction(self, _pr): pass
        def post_pr_comment_inline(self, _pr, _msg): pass

    result = asyncio.run(
        process_candidate(config, FakeClient(), store, object(), pr, event_log=event_log)
    )

    events = event_log.read_events()
    event_types = [e["event"] for e in events]
    assert "review_failed" in event_types
    failed = [e for e in events if e["event"] == "review_failed"][0]
    assert "clone failed" in failed["error"]

    meta = store.get_meta()
    assert meta.total_errors >= 1
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_processor_events.py::test_process_candidate_emits_review_events -v`
Expected: FAIL — `TypeError: process_candidate() got an unexpected keyword argument 'event_log'`

**Step 3: Write minimal implementation**

Modify `src/pr_reviewer/processor.py`:

Add import:
```python
from pr_reviewer.events import EventLog
from pr_reviewer.models import DaemonMeta
```

Update `process_candidate` signature (line 461) to add `event_log: EventLog | None = None`:

```python
async def process_candidate(
    config: AppConfig,
    client: GitHubClient,
    store: StateStore,
    workspace_mgr: PRWorkspace,
    pr: PRCandidate,
    *,
    event_log: EventLog | None = None,
    use_saved_review: bool = False,
    verbose: bool = True,
) -> ProcessingResult:
```

Add helper to update _meta after a review completes (add before `process_candidate`):

```python
def _update_meta_for_review(
    store: StateStore,
    *,
    processed: bool,
    error: bool,
    reviewer_outputs: dict[str, ReviewerOutput] | None = None,
    total_token_usage: TokenUsage | None = None,
) -> None:
    meta = store.get_meta()
    if processed:
        meta.total_prs_processed += 1
    else:
        meta.total_prs_skipped += 1
    if error:
        meta.total_errors += 1
    if total_token_usage is not None:
        meta.cumulative_input_tokens += total_token_usage.input_tokens
        meta.cumulative_output_tokens += total_token_usage.output_tokens
        if total_token_usage.cost_usd is not None:
            meta.cumulative_cost_usd += total_token_usage.cost_usd
    if reviewer_outputs is not None:
        for name, output in reviewer_outputs.items():
            stats = meta.per_reviewer_stats.get(name, {"success": 0, "error": 0, "total_duration_s": 0.0})
            if output.status == "ok":
                stats["success"] = stats.get("success", 0) + 1
            else:
                stats["error"] = stats.get("error", 0) + 1
            stats["total_duration_s"] = stats.get("total_duration_s", 0.0) + output.duration_seconds
            meta.per_reviewer_stats[name] = stats
    store.update_meta(meta)
```

Inside `process_candidate`, emit events at key points:

After triage completes (around line 583-590):
```python
        if event_log is not None:
            event_log.emit(
                "review_started",
                pr_key=pr.key,
                head_sha=pr.head_sha,
                triage_result=triage_result.value,
            )
```

After lightweight review completes (before `_publish_and_persist`, around line 620):
```python
            if event_log is not None:
                event_log.emit(
                    "review_completed",
                    pr_key=pr.key,
                    status="lightweight_generated",
                    total_cost_usd=lightweight_usage.cost_usd if lightweight_usage else None,
                )
            _update_meta_for_review(store, processed=True, error=False, total_token_usage=lightweight_usage)
```

After each reviewer completes in `_run_reviewers_with_monitoring` — instead, emit after the monitoring loop returns (around line 664-667). After `active_outputs` is built:
```python
        if event_log is not None:
            for name, output in active_outputs.items():
                event_log.emit(
                    "reviewer_completed",
                    pr_key=pr.key,
                    reviewer=name,
                    status=output.status,
                    duration_s=round(output.duration_seconds, 1),
                    input_tokens=output.token_usage.input_tokens if output.token_usage else 0,
                    output_tokens=output.token_usage.output_tokens if output.token_usage else 0,
                    cost_usd=output.token_usage.cost_usd if output.token_usage else None,
                    error=output.error,
                )
```

After full review completes (before final return, around line 744):
```python
        total_usage = _compute_total_token_usage(active_outputs, reconciler_usage)
        if event_log is not None:
            event_log.emit(
                "review_completed",
                pr_key=pr.key,
                status="generated",
                total_cost_usd=total_usage.cost_usd if total_usage else None,
                review_decision=review_decision,
            )
        _update_meta_for_review(
            store, processed=True, error=False,
            reviewer_outputs=active_outputs, total_token_usage=total_usage,
        )
```

In the `except` block (around line 756-766):
```python
        if event_log is not None:
            event_log.emit("review_failed", pr_key=pr.key, error=str(exc))
        _update_meta_for_review(store, processed=False, error=True)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_processor_events.py -v`
Expected: PASS

Then run the full suite to make sure nothing broke:
Run: `python -m pytest -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/processor.py tests/test_processor_events.py
git commit -m "feat: emit review events from processor, update _meta on completion"
```

---

### Task 7: Wire EventLog through cli.py

**Files:**
- Modify: `src/pr_reviewer/cli.py`

**Step 1: Modify `_load_runtime` to create EventLog**

In `src/pr_reviewer/cli.py`, add import:
```python
from pr_reviewer.events import EventLog
```

Change `_load_runtime` return type to `tuple[AppConfig, StateStore, EventLog]`. After creating `store`, add:

```python
    event_log_path = Path(config.state_file).parent / "events.jsonl"
    event_log = EventLog(event_log_path, max_bytes=config.max_event_log_bytes)
    return config, store, event_log
```

Update all callers (`run_once_command`, `start_command`) to unpack the third value and pass `event_log` to `process_candidate` / `start_daemon` / `run_cycle`.

**Step 2: Run full test suite**

Run: `python -m pytest -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add src/pr_reviewer/cli.py
git commit -m "feat: wire EventLog through CLI into daemon and processor"
```

---

### Task 8: CLI status command

**Files:**
- Modify: `src/pr_reviewer/cli.py`
- Test: `tests/test_cli_status.py`

**Step 1: Write the failing test**

Create `tests/test_cli_status.py`:

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from pr_reviewer.cli import app
from pr_reviewer.models import DaemonMeta
from pr_reviewer.state import StateStore

runner = CliRunner()


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text('github_orgs = ["test"]\n', encoding="utf-8")
    return config_path


def _write_state_with_meta(tmp_path: Path, meta: DaemonMeta) -> Path:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    state_path = state_dir / "pr-reviewer-state.json"
    store = StateStore(state_path)
    store.load()
    store.update_meta(meta)
    store.save()
    return state_path


def test_status_command_shows_daemon_info(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    meta = DaemonMeta(
        daemon_pid=12345,
        daemon_started_at="2026-03-05T10:00:00Z",
        total_cycles=42,
        total_prs_processed=15,
        total_prs_skipped=27,
        total_errors=3,
        cumulative_cost_usd=3.42,
        cumulative_input_tokens=450000,
        cumulative_output_tokens=85000,
    )
    _write_state_with_meta(tmp_path, meta)

    # Override state_file to point to our tmp location
    result = runner.invoke(app, [
        "status",
        "--config", str(config_path),
        "--state-file", str(tmp_path / ".state" / "pr-reviewer-state.json"),
    ])
    assert result.exit_code == 0
    assert "42" in result.output  # total_cycles
    assert "15" in result.output  # total_prs_processed


def test_status_command_json_output(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    meta = DaemonMeta(total_cycles=5, total_prs_processed=2)
    _write_state_with_meta(tmp_path, meta)

    result = runner.invoke(app, [
        "status",
        "--config", str(config_path),
        "--state-file", str(tmp_path / ".state" / "pr-reviewer-state.json"),
        "--output-format", "json",
    ])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total_cycles"] == 5
    assert data["total_prs_processed"] == 2
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_status.py -v`
Expected: FAIL — `No such command 'status'`

**Step 3: Write minimal implementation**

Add to `src/pr_reviewer/cli.py`:

```python
StateFileOption = Annotated[
    str | None,
    typer.Option(
        "--state-file",
        help="Override state_file from config.",
    ),
]


@app.command("status")
def status_command(
    config: ConfigOption = Path("config.toml"),
    state_file: StateFileOption = None,
    output_format: OutputFormatOption = "text",
) -> None:
    """Show daemon status and operational metrics."""
    import os

    cfg = load_config(config)
    resolved_state_file = state_file or cfg.state_file
    store = StateStore(Path(resolved_state_file))
    store.load()
    meta = store.get_meta()

    daemon_alive = False
    if meta.daemon_pid is not None:
        try:
            os.kill(meta.daemon_pid, 0)
            daemon_alive = True
        except (ProcessLookupError, PermissionError):
            pass

    if output_format == "json":
        import json as json_mod

        data = meta.to_dict()
        data["daemon_alive"] = daemon_alive
        print(json_mod.dumps(data, indent=2))
        return

    from rich.table import Table

    if daemon_alive:
        uptime = ""
        if meta.daemon_started_at:
            from datetime import UTC, datetime

            try:
                started = datetime.fromisoformat(
                    meta.daemon_started_at.replace("Z", "+00:00")
                )
                delta = datetime.now(UTC) - started
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes = remainder // 60
                uptime = f", up {hours}h {minutes}m"
            except ValueError:
                pass
        console.print(f"Daemon:     [green]running[/green] (pid {meta.daemon_pid}{uptime})")
    else:
        status_label = "not running" if meta.daemon_pid is None else f"dead (pid {meta.daemon_pid})"
        console.print(f"Daemon:     [red]{status_label}[/red]")

    if meta.last_cycle_at:
        console.print(f"Last cycle: {meta.last_cycle_at}")
    console.print(f"Cycles:     {meta.total_cycles} total, {meta.total_errors} errors")
    console.print()
    console.print(f"PRs:        {meta.total_prs_processed} processed, {meta.total_prs_skipped} skipped")

    def _fmt_tokens(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n // 1_000}K"
        return str(n)

    console.print(
        f"Cost:       ${meta.cumulative_cost_usd:.2f} "
        f"({_fmt_tokens(meta.cumulative_input_tokens)} input / "
        f"{_fmt_tokens(meta.cumulative_output_tokens)} output tokens)"
    )

    if meta.per_reviewer_stats:
        console.print()
        table = Table(title="Reviewers", show_header=True)
        table.add_column("Reviewer")
        table.add_column("Success", justify="right")
        table.add_column("Error", justify="right")
        table.add_column("Avg Duration", justify="right")
        for name, stats in sorted(meta.per_reviewer_stats.items()):
            success = stats.get("success", 0)
            err = stats.get("error", 0)
            total_dur = stats.get("total_duration_s", 0.0)
            total_runs = success + err
            avg = f"{total_dur / total_runs:.1f}s" if total_runs > 0 else "—"
            table.add_row(name, str(success), str(err), avg)
        console.print(table)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_status.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/cli.py tests/test_cli_status.py
git commit -m "feat: add pr-reviewer status CLI command"
```

---

### Task 9: CLI history command

**Files:**
- Modify: `src/pr_reviewer/cli.py`
- Test: `tests/test_cli_history.py`

**Step 1: Write the failing test**

Create `tests/test_cli_history.py`:

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from pr_reviewer.cli import app
from pr_reviewer.events import EventLog

runner = CliRunner()


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text('github_orgs = ["test"]\n', encoding="utf-8")
    return config_path


def test_history_command_shows_events(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    # Write empty state file
    (state_dir / "pr-reviewer-state.json").write_text("{}", encoding="utf-8")

    event_log = EventLog(state_dir / "events.jsonl")
    event_log.emit("review_completed", pr_key="org/repo#1", status="generated", total_cost_usd=0.12)
    event_log.emit("review_completed", pr_key="org/repo#2", status="lightweight_generated", total_cost_usd=0.02)

    result = runner.invoke(app, [
        "history",
        "--config", str(config_path),
        "--state-file", str(state_dir / "pr-reviewer-state.json"),
    ])
    assert result.exit_code == 0
    assert "org/repo#1" in result.output
    assert "org/repo#2" in result.output


def test_history_command_json_output(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "pr-reviewer-state.json").write_text("{}", encoding="utf-8")

    event_log = EventLog(state_dir / "events.jsonl")
    event_log.emit("review_completed", pr_key="org/repo#1", status="generated")

    result = runner.invoke(app, [
        "history",
        "--config", str(config_path),
        "--state-file", str(state_dir / "pr-reviewer-state.json"),
        "--output-format", "json",
    ])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) >= 1
    assert data[0]["pr_key"] == "org/repo#1"


def test_history_command_empty(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "pr-reviewer-state.json").write_text("{}", encoding="utf-8")

    result = runner.invoke(app, [
        "history",
        "--config", str(config_path),
        "--state-file", str(state_dir / "pr-reviewer-state.json"),
    ])
    assert result.exit_code == 0
    assert "No review events" in result.output
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_history.py -v`
Expected: FAIL — `No such command 'history'`

**Step 3: Write minimal implementation**

Add to `src/pr_reviewer/cli.py`:

```python
SinceOption = Annotated[
    str | None,
    typer.Option(
        "--since",
        help="Filter events since duration (e.g. 24h, 7d) or ISO timestamp.",
    ),
]
PrFilterOption = Annotated[
    str | None,
    typer.Option(
        "--pr",
        help="Filter to a specific PR (e.g. org/repo#42).",
    ),
]


def _parse_since(since: str | None) -> str | None:
    if since is None:
        return None
    from datetime import UTC, datetime, timedelta

    since = since.strip()
    if since.endswith("h"):
        delta = timedelta(hours=int(since[:-1]))
        return (datetime.now(UTC) - delta).isoformat()
    if since.endswith("d"):
        delta = timedelta(days=int(since[:-1]))
        return (datetime.now(UTC) - delta).isoformat()
    return since  # Assume ISO timestamp


@app.command("history")
def history_command(
    config: ConfigOption = Path("config.toml"),
    state_file: StateFileOption = None,
    since: SinceOption = None,
    pr: PrFilterOption = None,
    output_format: OutputFormatOption = "text",
) -> None:
    """Show review history from event log."""
    cfg = load_config(config)
    resolved_state_file = state_file or cfg.state_file
    event_log_path = Path(resolved_state_file).parent / "events.jsonl"
    event_log = EventLog(event_log_path)

    since_ts = _parse_since(since)
    events = event_log.read_events(since=since_ts)

    # Filter to review_completed and review_failed events
    review_events = [
        e for e in events
        if e.get("event") in ("review_completed", "review_failed")
    ]
    if pr:
        review_events = [e for e in review_events if e.get("pr_key") == pr]

    # Limit to most recent 50 by default
    if since is None and len(review_events) > 50:
        review_events = review_events[-50:]

    if output_format == "json":
        import json as json_mod

        print(json_mod.dumps(review_events, indent=2))
        return

    if not review_events:
        console.print("No review events found.")
        return

    from rich.table import Table

    table = Table(title="Review History", show_header=True)
    table.add_column("PR")
    table.add_column("Status")
    table.add_column("Cost", justify="right")
    table.add_column("When")
    for event in reversed(review_events):
        pr_key = event.get("pr_key", "?")
        status = event.get("status", event.get("event", "?"))
        cost = event.get("total_cost_usd")
        cost_str = f"${cost:.2f}" if cost is not None else "—"
        ts = event.get("ts", "?")
        table.add_row(pr_key, status, cost_str, ts)
    console.print(table)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_history.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/cli.py tests/test_cli_history.py
git commit -m "feat: add pr-reviewer history CLI command"
```

---

### Task 10: CLI costs command

**Files:**
- Modify: `src/pr_reviewer/cli.py`
- Test: `tests/test_cli_costs.py`

**Step 1: Write the failing test**

Create `tests/test_cli_costs.py`:

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from pr_reviewer.cli import app
from pr_reviewer.events import EventLog

runner = CliRunner()


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text('github_orgs = ["test"]\n', encoding="utf-8")
    return config_path


def _setup_events(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "pr-reviewer-state.json").write_text("{}", encoding="utf-8")
    event_log = EventLog(state_dir / "events.jsonl")
    event_log.emit(
        "reviewer_completed", pr_key="org/repo#1", reviewer="claude",
        status="ok", duration_s=90.0, input_tokens=10000, output_tokens=2000, cost_usd=0.08,
    )
    event_log.emit(
        "reviewer_completed", pr_key="org/repo#1", reviewer="codex",
        status="ok", duration_s=60.0, input_tokens=8000, output_tokens=1500, cost_usd=0.04,
    )
    event_log.emit(
        "review_completed", pr_key="org/repo#1", status="generated", total_cost_usd=0.12,
    )
    return state_dir


def test_costs_command_shows_breakdown(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    state_dir = _setup_events(tmp_path)

    result = runner.invoke(app, [
        "costs",
        "--config", str(config_path),
        "--state-file", str(state_dir / "pr-reviewer-state.json"),
        "--all",
    ])
    assert result.exit_code == 0
    assert "claude" in result.output
    assert "codex" in result.output


def test_costs_command_json_output(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    state_dir = _setup_events(tmp_path)

    result = runner.invoke(app, [
        "costs",
        "--config", str(config_path),
        "--state-file", str(state_dir / "pr-reviewer-state.json"),
        "--all",
        "--output-format", "json",
    ])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "by_reviewer" in data
    assert "total_cost_usd" in data
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_costs.py -v`
Expected: FAIL — `No such command 'costs'`

**Step 3: Write minimal implementation**

Add to `src/pr_reviewer/cli.py`:

```python
AllOption = Annotated[
    bool,
    typer.Option(
        "--all",
        help="Show costs for all time (not just --since window).",
    ),
]


@app.command("costs")
def costs_command(
    config: ConfigOption = Path("config.toml"),
    state_file: StateFileOption = None,
    since: SinceOption = None,
    all_time: AllOption = False,
    output_format: OutputFormatOption = "text",
) -> None:
    """Show token usage and cost breakdown."""
    cfg = load_config(config)
    resolved_state_file = state_file or cfg.state_file
    event_log_path = Path(resolved_state_file).parent / "events.jsonl"
    event_log = EventLog(event_log_path)

    if all_time:
        since_ts = None
    elif since:
        since_ts = _parse_since(since)
    else:
        since_ts = _parse_since("24h")

    events = event_log.read_events(since=since_ts)

    # Aggregate by reviewer
    by_reviewer: dict[str, dict] = {}
    for e in events:
        if e.get("event") != "reviewer_completed":
            continue
        name = e.get("reviewer", "unknown")
        entry = by_reviewer.setdefault(name, {
            "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "count": 0,
        })
        entry["cost_usd"] += e.get("cost_usd") or 0.0
        entry["input_tokens"] += e.get("input_tokens", 0)
        entry["output_tokens"] += e.get("output_tokens", 0)
        entry["count"] += 1

    # Aggregate by PR
    by_pr: dict[str, dict] = {}
    for e in events:
        if e.get("event") != "review_completed":
            continue
        pr_key = e.get("pr_key", "unknown")
        entry = by_pr.setdefault(pr_key, {"cost_usd": 0.0})
        entry["cost_usd"] += e.get("total_cost_usd") or 0.0

    total_cost = sum(r["cost_usd"] for r in by_reviewer.values())

    if output_format == "json":
        import json as json_mod

        print(json_mod.dumps({
            "by_reviewer": by_reviewer,
            "by_pr": by_pr,
            "total_cost_usd": round(total_cost, 4),
        }, indent=2))
        return

    def _fmt_tokens(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n // 1_000}K"
        return str(n)

    period = "all time" if all_time else (since or "last 24h")
    console.print(f"Period: {period}")
    console.print()

    if by_reviewer:
        from rich.table import Table

        table = Table(title="By Reviewer", show_header=True)
        table.add_column("Reviewer")
        table.add_column("Cost", justify="right")
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
        table.add_column("Reviews", justify="right")
        for name, data in sorted(by_reviewer.items()):
            table.add_row(
                name,
                f"${data['cost_usd']:.2f}",
                _fmt_tokens(data["input_tokens"]),
                _fmt_tokens(data["output_tokens"]),
                str(data["count"]),
            )
        console.print(table)
    else:
        console.print("No reviewer cost data found.")

    if by_pr:
        console.print()
        table2 = Table(title="By PR", show_header=True)
        table2.add_column("PR")
        table2.add_column("Cost", justify="right")
        for pr_key, data in sorted(by_pr.items()):
            table2.add_row(pr_key, f"${data['cost_usd']:.2f}")
        console.print(table2)

    console.print(f"\nTotal: ${total_cost:.2f}")
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_costs.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/cli.py tests/test_cli_costs.py
git commit -m "feat: add pr-reviewer costs CLI command"
```

---

### Task 11: HTTP API server

**Files:**
- Create: `src/pr_reviewer/server.py`
- Test: `tests/test_server.py`
- Modify: `src/pr_reviewer/cli.py` (add serve command)

**Step 1: Write the failing test**

Create `tests/test_server.py`:

```python
import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from pr_reviewer.events import EventLog
from pr_reviewer.models import DaemonMeta
from pr_reviewer.server import create_handler, make_server
from pr_reviewer.state import StateStore


@pytest.fixture()
def state_dir(tmp_path: Path):
    d = tmp_path / ".state"
    d.mkdir()
    state_path = d / "pr-reviewer-state.json"
    store = StateStore(state_path)
    store.load()
    meta = DaemonMeta(daemon_pid=99999, total_cycles=10, total_prs_processed=5)
    store.update_meta(meta)
    store.save()

    event_log = EventLog(d / "events.jsonl")
    event_log.emit("review_completed", pr_key="org/repo#1", status="generated", total_cost_usd=0.10)
    return d


@pytest.fixture()
def server_port(state_dir):
    state_path = state_dir / "pr-reviewer-state.json"
    event_log_path = state_dir / "events.jsonl"
    srv = make_server("127.0.0.1", 0, state_path, event_log_path)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port
    srv.shutdown()


def test_health_endpoint(server_port: int) -> None:
    conn = HTTPConnection("127.0.0.1", server_port)
    conn.request("GET", "/health")
    resp = conn.getresponse()
    assert resp.status == 200
    data = json.loads(resp.read())
    assert data["status"] == "ok"
    assert "daemon_pid" in data


def test_api_status_endpoint(server_port: int) -> None:
    conn = HTTPConnection("127.0.0.1", server_port)
    conn.request("GET", "/api/status")
    resp = conn.getresponse()
    assert resp.status == 200
    data = json.loads(resp.read())
    assert data["total_cycles"] == 10


def test_api_history_endpoint(server_port: int) -> None:
    conn = HTTPConnection("127.0.0.1", server_port)
    conn.request("GET", "/api/history")
    resp = conn.getresponse()
    assert resp.status == 200
    data = json.loads(resp.read())
    assert isinstance(data, list)
    assert len(data) >= 1


def test_api_costs_endpoint(server_port: int) -> None:
    conn = HTTPConnection("127.0.0.1", server_port)
    conn.request("GET", "/api/costs")
    resp = conn.getresponse()
    assert resp.status == 200
    data = json.loads(resp.read())
    assert "by_reviewer" in data
    assert "total_cost_usd" in data


def test_404_unknown_path(server_port: int) -> None:
    conn = HTTPConnection("127.0.0.1", server_port)
    conn.request("GET", "/unknown")
    resp = conn.getresponse()
    assert resp.status == 404
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pr_reviewer.server'`

**Step 3: Write minimal implementation**

Create `src/pr_reviewer/server.py`:

```python
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pr_reviewer.events import EventLog
from pr_reviewer.models import DaemonMeta
from pr_reviewer.state import StateStore


def _read_meta(state_path: Path) -> DaemonMeta:
    store = StateStore(state_path)
    store.load()
    return store.get_meta()


def _is_pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _parse_since_param(params: dict) -> str | None:
    since = params.get("since", [None])[0]
    if since is None:
        return None
    from datetime import UTC, datetime, timedelta

    since = since.strip()
    if since.endswith("h"):
        delta = timedelta(hours=int(since[:-1]))
        return (datetime.now(UTC) - delta).isoformat()
    if since.endswith("d"):
        delta = timedelta(days=int(since[:-1]))
        return (datetime.now(UTC) - delta).isoformat()
    return since


def create_handler(state_path: Path, event_log_path: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass  # Suppress default stderr logging

        def _json_response(self, data: object, status: int = 200) -> None:
            body = json.dumps(data, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            params = parse_qs(parsed.query)

            if path == "/health":
                meta = _read_meta(state_path)
                alive = _is_pid_alive(meta.daemon_pid)
                status_code = 200 if alive else 503
                self._json_response({
                    "status": "ok" if alive else "daemon_not_running",
                    "daemon_pid": meta.daemon_pid,
                    "daemon_alive": alive,
                    "last_cycle_at": meta.last_cycle_at,
                }, status=status_code)

            elif path == "/api/status":
                meta = _read_meta(state_path)
                data = meta.to_dict()
                data["daemon_alive"] = _is_pid_alive(meta.daemon_pid)
                self._json_response(data)

            elif path == "/api/history":
                event_log = EventLog(event_log_path)
                since_ts = _parse_since_param(params)
                events = event_log.read_events(since=since_ts)
                pr_filter = params.get("pr", [None])[0]
                review_events = [
                    e for e in events
                    if e.get("event") in ("review_completed", "review_failed")
                ]
                if pr_filter:
                    review_events = [e for e in review_events if e.get("pr_key") == pr_filter]
                self._json_response(review_events)

            elif path == "/api/costs":
                event_log = EventLog(event_log_path)
                since_ts = _parse_since_param(params)
                events = event_log.read_events(since=since_ts)

                by_reviewer: dict[str, dict] = {}
                for e in events:
                    if e.get("event") != "reviewer_completed":
                        continue
                    name = e.get("reviewer", "unknown")
                    entry = by_reviewer.setdefault(name, {
                        "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "count": 0,
                    })
                    entry["cost_usd"] += e.get("cost_usd") or 0.0
                    entry["input_tokens"] += e.get("input_tokens", 0)
                    entry["output_tokens"] += e.get("output_tokens", 0)
                    entry["count"] += 1

                by_pr: dict[str, dict] = {}
                for e in events:
                    if e.get("event") != "review_completed":
                        continue
                    pr_key = e.get("pr_key", "unknown")
                    entry = by_pr.setdefault(pr_key, {"cost_usd": 0.0})
                    entry["cost_usd"] += e.get("total_cost_usd") or 0.0

                total_cost = sum(r["cost_usd"] for r in by_reviewer.values())
                self._json_response({
                    "by_reviewer": by_reviewer,
                    "by_pr": by_pr,
                    "total_cost_usd": round(total_cost, 4),
                })

            else:
                self.send_response(404)
                self.end_headers()

    return Handler


def make_server(
    host: str, port: int, state_path: Path, event_log_path: Path,
) -> HTTPServer:
    handler = create_handler(state_path, event_log_path)
    return HTTPServer((host, port), handler)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_server.py -v`
Expected: PASS

**Step 5: Add serve command to CLI**

Add to `src/pr_reviewer/cli.py`:

```python
@app.command("serve")
def serve_command(
    config: ConfigOption = Path("config.toml"),
    state_file: StateFileOption = None,
    port: Annotated[int, typer.Option("--port", help="Port to listen on.")] = 9120,
    host: Annotated[str, typer.Option("--host", help="Host to bind to.")] = "127.0.0.1",
) -> None:
    """Start HTTP API server for status, history, and health checks."""
    from pr_reviewer.server import make_server

    cfg = load_config(config)
    resolved_state_file = state_file or cfg.state_file
    state_path = Path(resolved_state_file)
    event_log_path = state_path.parent / "events.jsonl"

    srv = make_server(host, port, state_path, event_log_path)
    info(f"Serving on http://{host}:{port}")
    info("Endpoints: /health, /api/status, /api/history, /api/costs")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        info("Shutting down server")
        srv.shutdown()
```

**Step 6: Run full test suite**

Run: `python -m pytest -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add src/pr_reviewer/server.py tests/test_server.py src/pr_reviewer/cli.py
git commit -m "feat: add HTTP API server with /health, /api/status, /api/history, /api/costs"
```

---

### Task 12: Update _meta in daemon cycle loop

**Files:**
- Modify: `src/pr_reviewer/daemon.py`

**Step 1: Write the failing test**

Add to `tests/test_daemon.py`:

```python
def test_run_cycle_updates_meta(monkeypatch, tmp_path) -> None:
    config = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    preflight = PreflightResult(viewer_login="inkvi")
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store._owns_lock = True
    store.load()
    event_log = EventLog(tmp_path / "events.jsonl")

    monkeypatch.setattr(
        "pr_reviewer.daemon.GitHubClient.discover_pr_candidates",
        lambda _self, _config: [],
    )

    asyncio.run(run_cycle(config, preflight, store, event_log=event_log, verbose=False))

    meta = store.get_meta()
    assert meta.total_cycles == 1
    assert meta.last_cycle_at is not None
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_daemon.py::test_run_cycle_updates_meta -v`
Expected: FAIL — `assert 0 == 1` (meta not updated yet)

**Step 3: Write minimal implementation**

In `daemon.py`, at the end of `run_cycle` (before each return), update `_meta`:

```python
    # Update _meta at end of cycle
    if isinstance(store, StateStore):
        meta = store.get_meta()
        meta.total_cycles += 1
        meta.last_cycle_at = ProcessedState.now_iso()
        store.update_meta(meta)
        store.save()
```

Add the import for `ProcessedState`:
```python
from pr_reviewer.models import PRCandidate, ProcessedState
```

Place this block right before each `return processed` in `run_cycle`.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `python -m pytest -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/pr_reviewer/daemon.py tests/test_daemon.py
git commit -m "feat: update _meta cycle counters in daemon run_cycle"
```

---

### Task 13: Update _meta daemon_started_at/pid on start

**Files:**
- Modify: `src/pr_reviewer/daemon.py`

**Step 1: Write the failing test**

Add to `tests/test_daemon.py`:

```python
def test_start_daemon_sets_meta_pid(monkeypatch, tmp_path) -> None:
    config = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    preflight = PreflightResult(viewer_login="inkvi")
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store._owns_lock = True
    store.load()
    event_log = EventLog(tmp_path / "events.jsonl")

    async def fake_run_cycle(_config, _preflight, _store, *, event_log=None, verbose=True):
        return 0

    async def fake_sleep(_seconds):
        raise RuntimeError("stop")

    monkeypatch.setattr("pr_reviewer.daemon.run_cycle", fake_run_cycle)
    monkeypatch.setattr("pr_reviewer.daemon.asyncio.sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop"):
        asyncio.run(start_daemon(config, preflight, store, event_log=event_log))

    meta = store.get_meta()
    assert meta.daemon_pid == os.getpid()
    assert meta.daemon_started_at is not None
```

Add `import os` at the top of `tests/test_daemon.py`.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_daemon.py::test_start_daemon_sets_meta_pid -v`
Expected: FAIL

**Step 3: Write minimal implementation**

In `daemon.py`, inside `start_daemon`, after emitting the `daemon_started` event, add:

```python
    if isinstance(store, StateStore):
        meta = store.get_meta()
        meta.daemon_started_at = ProcessedState.now_iso()
        meta.daemon_pid = os.getpid()
        store.update_meta(meta)
        store.save()
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_daemon.py -v`
Expected: PASS

**Step 5: Run full suite**

Run: `python -m pytest -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/pr_reviewer/daemon.py tests/test_daemon.py
git commit -m "feat: set daemon_pid and daemon_started_at in _meta on startup"
```

---

### Task 14: Final integration test and cleanup

**Step 1: Run the full test suite**

Run: `python -m pytest -v`
Expected: All PASS

**Step 2: Run ruff linter**

Run: `ruff check src/ tests/`
Expected: No errors (fix any that appear)

**Step 3: Run ruff formatter**

Run: `ruff format src/ tests/`

**Step 4: Verify CLI commands work**

Run: `python -m pr_reviewer status --config config.example.toml` (expect graceful output even without state file)

Run: `python -m pr_reviewer history --config config.example.toml` (expect "No review events")

Run: `python -m pr_reviewer costs --config config.example.toml --all` (expect empty output)

**Step 5: Commit any lint fixes**

```bash
git add -u
git commit -m "chore: lint and format fixes"
```
