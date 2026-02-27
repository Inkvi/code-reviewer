import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pr_reviewer.config import AppConfig
from pr_reviewer.models import PRCandidate, ReviewerOutput
from pr_reviewer.processor import _single_reviewer_final_review, _start_codex_review_task


def test_single_reviewer_final_review_uses_markdown_when_ok() -> None:
    now = datetime.now(UTC)
    output = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="### Findings\n- [P3] file.rs:1 - nit.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    final_review = _single_reviewer_final_review(output)
    assert "[P3]" in final_review


def test_single_reviewer_final_review_returns_failure_template() -> None:
    now = datetime.now(UTC)
    output = ReviewerOutput(
        reviewer="codex",
        status="error",
        markdown="",
        stdout="",
        stderr="failure",
        error="codex failed",
        started_at=now,
        ended_at=now,
    )

    final_review = _single_reviewer_final_review(output)
    assert "Reviewer failed" in final_review
    assert "codex failed" in final_review
    assert "### Test Gaps" in final_review


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
    )


async def _ok_output(name: str) -> ReviewerOutput:
    now = datetime.now(UTC)
    return ReviewerOutput(
        reviewer=name,
        status="ok",
        markdown=f"{name} output",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )


def test_start_codex_review_task_uses_cli_backend(monkeypatch) -> None:
    async def fake_codex_cli(pr, workdir, timeout_seconds):  # noqa: ANN001
        assert pr.number == 64
        assert workdir == Path("/tmp/repo")
        assert timeout_seconds == 30
        return await _ok_output("codex")

    async def fake_codex_agents(*_args, **_kwargs):  # noqa: ANN001
        raise AssertionError("agents backend should not be called")

    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", fake_codex_cli)
    monkeypatch.setattr("pr_reviewer.processor.run_codex_review_via_agents_sdk", fake_codex_agents)

    cfg = AppConfig(
        github_org="polymerdao",
        enabled_reviewers=["codex"],
        codex_backend="cli",
        codex_timeout_seconds=30,
    )
    async def _run() -> ReviewerOutput:
        task = _start_codex_review_task(cfg, _sample_pr(), Path("/tmp/repo"))
        return await task

    output = asyncio.run(_run())

    assert output.status == "ok"
    assert output.markdown == "codex output"


def test_start_codex_review_task_uses_agents_backend(monkeypatch) -> None:
    async def fake_codex_cli(*_args, **_kwargs):  # noqa: ANN001
        raise AssertionError("cli backend should not be called")

    async def fake_codex_agents(pr, workdir, timeout_seconds, model):  # noqa: ANN001
        assert pr.number == 64
        assert workdir == Path("/tmp/repo")
        assert timeout_seconds == 30
        assert model == "gpt-5.3-codex"
        return await _ok_output("codex")

    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", fake_codex_cli)
    monkeypatch.setattr("pr_reviewer.processor.run_codex_review_via_agents_sdk", fake_codex_agents)

    cfg = AppConfig(
        github_org="polymerdao",
        enabled_reviewers=["codex"],
        codex_backend="agents_sdk",
        codex_timeout_seconds=30,
    )
    async def _run() -> ReviewerOutput:
        task = _start_codex_review_task(cfg, _sample_pr(), Path("/tmp/repo"))
        return await task

    output = asyncio.run(_run())

    assert output.status == "ok"
    assert output.markdown == "codex output"
