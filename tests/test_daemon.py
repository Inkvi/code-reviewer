import asyncio

import pytest

from pr_reviewer.config import AppConfig
from pr_reviewer.daemon import run_cycle, start_daemon
from pr_reviewer.github import GitHubClient
from pr_reviewer.models import PRCandidate, SlashCommandTrigger
from pr_reviewer.preflight import PreflightResult
from pr_reviewer.state import StateStore


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


def test_run_cycle_quiet_mode_suppresses_per_pr_logs(monkeypatch) -> None:
    config = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    preflight = PreflightResult(viewer_login="inkvi")
    pr = _sample_pr(13)

    logs: list[str] = []
    verbose_args: list[bool] = []

    monkeypatch.setattr("pr_reviewer.daemon.info", logs.append)
    monkeypatch.setattr(
        "pr_reviewer.daemon.GitHubClient.discover_pr_candidates",
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
    ) -> bool:
        verbose_args.append(verbose)
        return False

    monkeypatch.setattr("pr_reviewer.daemon.process_candidate", fake_process_candidate)

    processed = asyncio.run(run_cycle(config, preflight, object(), verbose=False))

    assert processed == 0
    assert verbose_args == [False]
    assert logs == []


def test_start_daemon_uses_quiet_run_cycle(monkeypatch) -> None:
    config = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    preflight = PreflightResult(viewer_login="inkvi")
    run_cycle_verbose_args: list[bool] = []

    async def fake_run_cycle(_config, _preflight, _store, *, verbose=True) -> int:  # noqa: ANN001
        run_cycle_verbose_args.append(verbose)
        return 0

    async def fake_sleep(_seconds: int) -> None:
        raise RuntimeError("stop daemon loop")

    monkeypatch.setattr("pr_reviewer.daemon.run_cycle", fake_run_cycle)
    monkeypatch.setattr("pr_reviewer.daemon.asyncio.sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop daemon loop"):
        asyncio.run(start_daemon(config, preflight, object()))

    assert run_cycle_verbose_args == [False]


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
        return True

    monkeypatch.setattr("pr_reviewer.daemon.process_candidate", fake_process)

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
        return True

    monkeypatch.setattr("pr_reviewer.daemon.process_candidate", fake_process)

    asyncio.run(run_cycle(config, preflight, store))

    assert len(processed_prs) == 1
    assert processed_prs[0].slash_command_trigger is not None
    assert processed_prs[0].slash_command_trigger.comment_id == 999
