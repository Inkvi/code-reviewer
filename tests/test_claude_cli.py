import asyncio
from pathlib import Path

import pytest

from code_reviewer.models import PRCandidate
from code_reviewer.reviewers.claude_cli import (
    _build_claude_cli_command,
    run_claude_cli_prompt,
    run_claude_cli_review,
)


def test_build_claude_cli_command_basic() -> None:
    args = _build_claude_cli_command("Say hello")
    assert args == [
        "claude",
        "-p",
        "Say hello",
        "--output-format",
        "text",
        "--dangerously-skip-permissions",
    ]


def test_build_claude_cli_command_with_model() -> None:
    args = _build_claude_cli_command("Say hello", model="claude-sonnet-4-5")
    assert "--model" in args
    assert "claude-sonnet-4-5" in args


def test_build_claude_cli_command_with_system_prompt() -> None:
    args = _build_claude_cli_command("Say hello", system_prompt="You are a reviewer.")
    assert "--system-prompt" in args
    assert "You are a reviewer." in args


def test_build_claude_cli_command_with_max_turns() -> None:
    args = _build_claude_cli_command("Say hello", max_turns=1)
    assert "--max-turns" in args
    assert "1" in args


def test_run_claude_cli_prompt_success(monkeypatch) -> None:
    async def fake_run(args, *, cwd=None, timeout=None, env=None):  # noqa: ANN001
        assert args[0] == "claude"
        assert "--dangerously-skip-permissions" in args
        assert env == {"CLAUDECODE": ""}
        return 0, "### Findings\n- No issues.", ""

    monkeypatch.setattr("code_reviewer.reviewers.claude_cli.run_command_async", fake_run)

    text, usage = asyncio.run(run_claude_cli_prompt("review this", Path("/tmp/repo"), 60))
    assert text == "### Findings\n- No issues."
    assert usage is None


def test_run_claude_cli_prompt_timeout(monkeypatch) -> None:
    async def fake_run(args, *, cwd=None, timeout=None, env=None):  # noqa: ANN001
        raise TimeoutError()

    monkeypatch.setattr("code_reviewer.reviewers.claude_cli.run_command_async", fake_run)

    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(run_claude_cli_prompt("review this", Path("/tmp/repo"), 60))


def test_run_claude_cli_prompt_nonzero_exit(monkeypatch) -> None:
    async def fake_run(args, *, cwd=None, timeout=None, env=None):  # noqa: ANN001
        return 1, "", "something went wrong"

    monkeypatch.setattr("code_reviewer.reviewers.claude_cli.run_command_async", fake_run)

    with pytest.raises(RuntimeError, match="status 1"):
        asyncio.run(run_claude_cli_prompt("review this", Path("/tmp/repo"), 60))


def test_run_claude_cli_prompt_empty(monkeypatch) -> None:
    async def fake_run(args, *, cwd=None, timeout=None, env=None):  # noqa: ANN001
        return 0, "   ", ""

    monkeypatch.setattr("code_reviewer.reviewers.claude_cli.run_command_async", fake_run)

    with pytest.raises(RuntimeError, match="empty response"):
        asyncio.run(run_claude_cli_prompt("review this", Path("/tmp/repo"), 60))


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


def test_run_claude_cli_review_ok(monkeypatch, tmp_path: Path) -> None:
    async def fake_prompt(
        prompt,
        cwd,
        timeout,
        *,
        system_prompt=None,
        max_turns=None,
        model=None,
        reasoning_effort=None,
    ):  # noqa: ANN001
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.", None

    monkeypatch.setattr("code_reviewer.reviewers.claude_cli.run_claude_cli_prompt", fake_prompt)

    result = asyncio.run(run_claude_cli_review(_sample_pr(), tmp_path, 60))
    assert result.status == "ok"
    assert result.reviewer == "claude"
    assert "### Findings" in result.markdown


def test_run_claude_cli_review_error(monkeypatch, tmp_path: Path) -> None:
    async def fake_prompt(
        prompt,
        cwd,
        timeout,
        *,
        system_prompt=None,
        max_turns=None,
        model=None,
        reasoning_effort=None,
    ):  # noqa: ANN001
        raise RuntimeError("CLI crashed")

    monkeypatch.setattr("code_reviewer.reviewers.claude_cli.run_claude_cli_prompt", fake_prompt)

    result = asyncio.run(run_claude_cli_review(_sample_pr(), tmp_path, 60))
    assert result.status == "error"
    assert "CLI crashed" in result.error
