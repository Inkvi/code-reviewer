import asyncio
import json
from pathlib import Path

import pytest

from code_reviewer.models import PRCandidate
from code_reviewer.reviewers.pi_cli import (
    _build_pi_command,
    _parse_pi_events,
    run_pi_prompt,
    run_pi_review,
)


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="acme",
        repo="widgets",
        number=7,
        url="https://github.com/acme/widgets/pull/7",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-10-01T00:00:00Z",
    )


def _assistant(content: list[dict], **extra: object) -> dict:
    return {"role": "assistant", "content": content, "stopReason": "stop", **extra}


def _jsonl(*events: dict) -> str:
    return "".join(json.dumps(e) + "\n" for e in events)


def _session(final: dict) -> str:
    """A minimal pi --mode json run that ends with the given assistant message."""
    user = {"role": "user", "content": [{"type": "text", "text": "prompt"}]}
    return _jsonl(
        {"type": "session", "version": 3, "id": "s1"},
        {"type": "agent_start"},
        {"type": "message_end", "message": user},
        {"type": "message_update", "message": final},
        {"type": "message_end", "message": final},
        {"type": "agent_end", "messages": [user, final]},
    )


def test_build_pi_command_with_model() -> None:
    args = _build_pi_command("review this", model="openrouter/qwen/qwen3-coder:high")
    assert args == [
        "pi",
        "-p",
        "--mode",
        "json",
        "--no-session",
        "--model",
        "openrouter/qwen/qwen3-coder:high",
        "review this",
    ]


def test_build_pi_command_without_model() -> None:
    assert "--model" not in _build_pi_command("review this")


def test_parse_pi_events_skips_malformed_lines() -> None:
    raw = '{"type":"agent_start"}\nnot json\n\n{"type":"agent_end","messages":[]}\n'
    assert [e["type"] for e in _parse_pi_events(raw)] == ["agent_start", "agent_end"]


def test_run_pi_prompt_returns_last_assistant_text(monkeypatch, tmp_path: Path) -> None:
    tool_turn = _assistant([{"type": "text", "text": "Let me look."}], stopReason="toolUse")
    final = _assistant(
        [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "### Findings\n- None."},
        ]
    )
    stdout = _jsonl({"type": "message_end", "message": tool_turn}) + _session(final)

    async def fake_run(args, cwd, timeout):  # noqa: ANN001
        return (0, stdout, "")

    monkeypatch.setattr("code_reviewer.reviewers.pi_cli.run_command_async", fake_run)
    text, conversation = asyncio.run(run_pi_prompt("prompt", tmp_path, 60))

    assert text == "### Findings\n- None."
    assert conversation is not None
    assert all(e["type"] != "message_update" for e in conversation)


def test_run_pi_prompt_raises_on_error_stop_reason(monkeypatch, tmp_path: Path) -> None:
    # pi exits 0 when the model API call fails.
    final = _assistant([], stopReason="error", errorMessage="401 Missing Authentication header")

    async def fake_run(args, cwd, timeout):  # noqa: ANN001
        return (0, _session(final), "")

    monkeypatch.setattr("code_reviewer.reviewers.pi_cli.run_command_async", fake_run)
    with pytest.raises(RuntimeError, match="pi failed: 401 Missing Authentication header"):
        asyncio.run(run_pi_prompt("prompt", tmp_path, 60))


def test_run_pi_prompt_raises_on_nonzero_exit(monkeypatch, tmp_path: Path) -> None:
    async def fake_run(args, cwd, timeout):  # noqa: ANN001
        return (1, "", "boom")

    monkeypatch.setattr("code_reviewer.reviewers.pi_cli.run_command_async", fake_run)
    with pytest.raises(RuntimeError, match="pi exited with status 1: boom"):
        asyncio.run(run_pi_prompt("prompt", tmp_path, 60))


def test_run_pi_prompt_raises_on_empty_response(monkeypatch, tmp_path: Path) -> None:
    async def fake_run(args, cwd, timeout):  # noqa: ANN001
        return (0, _session(_assistant([])), "")

    monkeypatch.setattr("code_reviewer.reviewers.pi_cli.run_command_async", fake_run)
    with pytest.raises(RuntimeError, match="empty response"):
        asyncio.run(run_pi_prompt("prompt", tmp_path, 60))


def test_run_pi_prompt_raises_on_timeout(monkeypatch, tmp_path: Path) -> None:
    async def fake_run(args, cwd, timeout):  # noqa: ANN001
        raise TimeoutError()

    monkeypatch.setattr("code_reviewer.reviewers.pi_cli.run_command_async", fake_run)
    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(run_pi_prompt("prompt", tmp_path, 60))


def test_run_pi_review_ok(monkeypatch, tmp_path: Path) -> None:
    final = _assistant([{"type": "text", "text": "### Findings\n- No issues."}])

    async def fake_run(args, cwd, timeout):  # noqa: ANN001
        return (0, _session(final), "")

    monkeypatch.setattr("code_reviewer.reviewers.pi_cli.run_command_async", fake_run)
    result = asyncio.run(run_pi_review(_sample_pr(), tmp_path, 120))

    assert result.reviewer == "pi"
    assert result.status == "ok"
    assert "### Findings" in result.markdown


def test_run_pi_review_error(monkeypatch, tmp_path: Path) -> None:
    final = _assistant([], stopReason="error", errorMessage="400 not a valid model ID")

    async def fake_run(args, cwd, timeout):  # noqa: ANN001
        return (0, _session(final), "")

    monkeypatch.setattr("code_reviewer.reviewers.pi_cli.run_command_async", fake_run)
    result = asyncio.run(run_pi_review(_sample_pr(), tmp_path, 120))

    assert result.reviewer == "pi"
    assert result.status == "error"
    assert "not a valid model ID" in (result.error or "")
