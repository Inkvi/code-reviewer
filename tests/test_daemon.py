import asyncio

import pytest

from pr_reviewer.config import AppConfig
from pr_reviewer.daemon import run_cycle, start_daemon
from pr_reviewer.models import PRCandidate
from pr_reviewer.preflight import PreflightResult


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
