import asyncio

from code_reviewer.config import AppConfig
from code_reviewer.daemon import (
    _discover_candidates,
    _discovery_loop,
    _worker,
    run_cycle,
    start_daemon,
)
from code_reviewer.github import GitHubClient
from code_reviewer.models import PRCandidate, ProcessingResult, SlashCommandTrigger
from code_reviewer.preflight import PreflightResult
from code_reviewer.state import StateStore


def _sample_pr(number: int) -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="bridge-master",
        number=number,
        url=f"https://github.com/polymerdao/bridge-master/pull/{number}",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha=f"deadbeef{number}",
        updated_at="2026-03-01T00:00:00Z",
    )


def test_discover_candidates_merges_slash_commands(monkeypatch, tmp_path) -> None:
    """_discover_candidates returns merged list of regular + slash command PRs."""
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=True)
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store._owns_lock = True
    store.load()

    regular_pr = _sample_pr(1)
    slash_pr = _sample_pr(2)
    slash_pr.slash_command_trigger = SlashCommandTrigger(
        comment_id=100, comment_author="bob", comment_created_at="2026-03-05T10:00:00Z"
    )

    monkeypatch.setattr(GitHubClient, "discover_pr_candidates", lambda _self, _config: [regular_pr])
    monkeypatch.setattr(
        GitHubClient,
        "discover_slash_command_candidates",
        lambda _self, _config, _store: [slash_pr],
    )

    client = GitHubClient(viewer_login="inkvi")
    candidates = asyncio.run(_discover_candidates(config, client, store))
    keys = [c.key for c in candidates]
    assert "polymerdao/bridge-master#1" in keys
    assert "polymerdao/bridge-master#2" in keys


def test_discovery_loop_enqueues_candidates(monkeypatch) -> None:
    """Discovery loop enqueues new candidates and skips already-scheduled ones."""
    config = AppConfig(github_orgs=["polymerdao"], poll_interval_seconds=15)
    preflight = PreflightResult(viewer_login="inkvi")

    pr1 = _sample_pr(1)
    pr2 = _sample_pr(2)

    monkeypatch.setattr("code_reviewer.daemon.refresh_github_token", lambda: None)

    queue: asyncio.Queue[PRCandidate | None] = asyncio.Queue()
    scheduled: set[str] = set()
    # Pre-schedule pr1 to verify it gets skipped
    scheduled.add(pr1.key)
    shutdown = asyncio.Event()

    async def fake_discover(_config, _client, _store):
        return [pr1, pr2]

    monkeypatch.setattr("code_reviewer.daemon._discover_candidates", fake_discover)

    async def run() -> None:
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

    asyncio.run(_worker([config], preflight, object(), queue, scheduled))

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

    asyncio.run(_worker([config], preflight, object(), queue, scheduled))

    assert pr.key not in scheduled


def test_run_cycle_quiet_mode_suppresses_per_pr_logs(monkeypatch) -> None:
    config = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    preflight = PreflightResult(viewer_login="inkvi")
    pr = _sample_pr(13)

    logs: list[str] = []
    verbose_args: list[bool] = []

    monkeypatch.setattr("code_reviewer.daemon.info", logs.append)
    monkeypatch.setattr(
        "code_reviewer.daemon.GitHubClient.discover_pr_candidates",
        lambda _self, _config: [pr],
    )

    async def fake_process_candidate(  # noqa: ANN001
        _config,
        _client,
        _store,
        _workspace_mgr,
        _pr,
        *,
        verbose=True,
        **_kwargs,
    ) -> ProcessingResult:
        verbose_args.append(verbose)
        return ProcessingResult(
            processed=False,
            pr_url=_pr.url,
            pr_key=_pr.key,
            status="skipped",
        )

    monkeypatch.setattr("code_reviewer.daemon.process_candidate", fake_process_candidate)

    processed = asyncio.run(run_cycle(config, preflight, object(), verbose=False))

    assert processed == 0
    assert verbose_args == [False]
    assert logs == []


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
            for _ in range(50):
                await asyncio.sleep(0.02)
                if processed_keys:
                    break
            shutdown.set()

        asyncio.create_task(stop_after_processing())
        await start_daemon(config, preflight, object(), shutdown_event=shutdown)

    asyncio.run(run())

    assert pr.key in processed_keys


def test_run_cycle_merges_slash_command_candidates(monkeypatch, tmp_path) -> None:
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=True)
    preflight = PreflightResult(viewer_login="Inkvi")
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store._owns_lock = True
    store.load()

    reviewer_assigned_pr = PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="assigned pr",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-03-05T10:00:00Z",
        additions=20,
        deletions=5,
        changed_file_paths=["src/app.py"],
    )

    slash_pr = PRCandidate(
        owner="polymerdao",
        repo="bridge",
        number=10,
        url="https://github.com/polymerdao/bridge/pull/10",
        title="slash pr",
        author_login="bob",
        base_ref="main",
        head_sha="cafe1234",
        updated_at="2026-03-05T10:01:00Z",
        additions=15,
        deletions=3,
        changed_file_paths=["src/main.py"],
        slash_command_trigger=SlashCommandTrigger(
            comment_id=999,
            comment_author="bob",
            comment_created_at="2026-03-05T10:01:00+00:00",
        ),
    )

    monkeypatch.setattr(
        GitHubClient,
        "discover_pr_candidates",
        lambda _self, _config: [reviewer_assigned_pr],
    )
    monkeypatch.setattr(
        GitHubClient,
        "discover_slash_command_candidates",
        lambda _self, _config, _store: [slash_pr],
    )

    processed_keys: list[str] = []

    async def fake_process(_config, _client, _store, _workspace, pr, **_kwargs):
        processed_keys.append(pr.key)
        return ProcessingResult(
            processed=True,
            pr_url=pr.url,
            pr_key=pr.key,
            status="generated",
        )

    monkeypatch.setattr("code_reviewer.daemon.process_candidate", fake_process)

    processed = asyncio.run(run_cycle(config, preflight, store))

    assert processed == 2
    assert "polymerdao/obul#64" in processed_keys
    assert "polymerdao/bridge#10" in processed_keys


def test_run_cycle_slash_command_replaces_existing_candidate(monkeypatch, tmp_path) -> None:
    """When a PR appears in both discovery paths, the slash command version wins."""
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=True)
    preflight = PreflightResult(viewer_login="Inkvi")
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store._owns_lock = True
    store.load()

    regular_pr = PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-03-05T10:00:00Z",
        additions=20,
        deletions=5,
        changed_file_paths=["src/app.py"],
    )

    slash_pr = PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-03-05T10:00:00Z",
        additions=20,
        deletions=5,
        changed_file_paths=["src/app.py"],
        slash_command_trigger=SlashCommandTrigger(
            comment_id=999,
            comment_author="alice",
            comment_created_at="2026-03-05T10:01:00+00:00",
        ),
    )

    monkeypatch.setattr(
        GitHubClient,
        "discover_pr_candidates",
        lambda _self, _config: [regular_pr],
    )
    monkeypatch.setattr(
        GitHubClient,
        "discover_slash_command_candidates",
        lambda _self, _config, _store: [slash_pr],
    )

    processed_prs: list[PRCandidate] = []

    async def fake_process(_config, _client, _store, _workspace, pr, **_kwargs):
        processed_prs.append(pr)
        return ProcessingResult(
            processed=True,
            pr_url=pr.url,
            pr_key=pr.key,
            status="generated",
        )

    monkeypatch.setattr("code_reviewer.daemon.process_candidate", fake_process)

    asyncio.run(run_cycle(config, preflight, store))

    assert len(processed_prs) == 1
    assert processed_prs[0].slash_command_trigger is not None
    assert processed_prs[0].slash_command_trigger.comment_id == 999
