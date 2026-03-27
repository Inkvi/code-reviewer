import asyncio
from pathlib import Path

import pytest

from code_reviewer.models import PRCandidate
from code_reviewer.reviewers.opencode_cli import (
    _build_opencode_command,
    _extract_opencode_text,
    run_opencode_prompt,
    run_opencode_review,
)


def test_extract_text_from_jsonl() -> None:
    jsonl = (
        '{"type":"step_start","timestamp":1,"sessionID":"s1","part":{"type":"step-start"}}\n'
        '{"type":"text","timestamp":2,"sessionID":"s1","part":{"type":"text","text":"hello world"}}\n'
        '{"type":"step_finish","timestamp":3,"sessionID":"s1","part":{"type":"step-finish","cost":0.005,"tokens":{"total":100,"input":90,"output":10}}}\n'
    )
    text = _extract_opencode_text(jsonl)
    assert text == "hello world"


def test_extract_text_concatenates_multiple_text_events() -> None:
    jsonl = (
        '{"type":"text","timestamp":1,"sessionID":"s1","part":{"type":"text","text":"part one"}}\n'
        '{"type":"text","timestamp":2,"sessionID":"s1","part":{"type":"text","text":"part two"}}\n'
    )
    text = _extract_opencode_text(jsonl)
    assert text == "part one\npart two"


def test_extract_text_empty_on_no_text_events() -> None:
    jsonl = '{"type":"step_start","timestamp":1,"sessionID":"s1","part":{"type":"step-start"}}\n'
    text = _extract_opencode_text(jsonl)
    assert text == ""


def test_extract_text_skips_malformed_lines() -> None:
    jsonl = (
        "not json\n"
        '{"type":"text","timestamp":1,"sessionID":"s1","part":{"type":"text","text":"valid"}}\n'
    )
    text = _extract_opencode_text(jsonl)
    assert text == "valid"


def test_extract_text_skips_tool_use_events() -> None:
    jsonl = (
        '{"type":"tool_use","timestamp":1,"sessionID":"s1","part":{"type":"tool","tool":"bash"}}\n'
        '{"type":"text","timestamp":2,"sessionID":"s1","part":{"type":"text","text":"result"}}\n'
    )
    text = _extract_opencode_text(jsonl)
    assert text == "result"


def test_build_command_without_model() -> None:
    args = _build_opencode_command("Review this code")
    assert args[0] == "opencode"
    assert "run" in args
    assert "--format" in args
    fmt_idx = args.index("--format")
    assert args[fmt_idx + 1] == "json"
    assert "-m" not in args
    assert args[-1] == "Review this code"


def test_build_command_with_model() -> None:
    args = _build_opencode_command("Review this code", model="openrouter/zhipu/glm-5")
    assert "-m" in args
    m_idx = args.index("-m")
    assert args[m_idx + 1] == "openrouter/zhipu/glm-5"


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="testorg",
        repo="testrepo",
        number=42,
        url="https://github.com/testorg/testrepo/pull/42",
        title="test PR",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-03-26T00:00:00Z",
    )


def test_run_opencode_prompt_returns_text(monkeypatch, tmp_path: Path) -> None:
    jsonl_output = (
        '{"type":"text","timestamp":1,"sessionID":"s1","part":{"type":"text","text":"review output"}}\n'
        '{"type":"step_finish","timestamp":2,"sessionID":"s1","part":{"type":"step-finish","cost":0.01,"tokens":{"total":500,"input":490,"output":10}}}\n'
    )

    async def fake_run(args, cwd, timeout):
        return (0, jsonl_output, "")

    monkeypatch.setattr("code_reviewer.reviewers.opencode_cli.run_command_async", fake_run)
    text, conv = asyncio.run(run_opencode_prompt("prompt", tmp_path, 60))
    assert text == "review output"
    assert conv is not None


def test_run_opencode_prompt_raises_on_nonzero_exit(monkeypatch, tmp_path: Path) -> None:
    async def fake_run(args, cwd, timeout):
        return (1, "", "something went wrong")

    monkeypatch.setattr("code_reviewer.reviewers.opencode_cli.run_command_async", fake_run)
    with pytest.raises(RuntimeError, match="opencode exited with status 1"):
        asyncio.run(run_opencode_prompt("prompt", tmp_path, 60))


def test_run_opencode_prompt_raises_on_empty_response(monkeypatch, tmp_path: Path) -> None:
    jsonl_output = (
        '{"type":"step_finish","timestamp":1,"sessionID":"s1","part":{"type":"step-finish"}}\n'
    )

    async def fake_run(args, cwd, timeout):
        return (0, jsonl_output, "")

    monkeypatch.setattr("code_reviewer.reviewers.opencode_cli.run_command_async", fake_run)
    with pytest.raises(RuntimeError, match="empty response"):
        asyncio.run(run_opencode_prompt("prompt", tmp_path, 60))


def test_run_opencode_prompt_raises_on_timeout(monkeypatch, tmp_path: Path) -> None:
    async def fake_run(args, cwd, timeout):
        raise TimeoutError()

    monkeypatch.setattr("code_reviewer.reviewers.opencode_cli.run_command_async", fake_run)
    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(run_opencode_prompt("prompt", tmp_path, 60))


def test_run_opencode_review_ok(monkeypatch, tmp_path: Path) -> None:
    pr = _sample_pr()
    jsonl_output = (
        '{"type":"text","timestamp":1,"sessionID":"s1","part":{"type":"text","text":"### Findings\\n- No issues."}}\n'
        '{"type":"step_finish","timestamp":2,"sessionID":"s1","part":{"type":"step-finish","cost":0.01}}\n'
    )

    async def fake_run(args, cwd, timeout):
        return (0, jsonl_output, "")

    monkeypatch.setattr("code_reviewer.reviewers.opencode_cli.run_command_async", fake_run)

    result = asyncio.run(run_opencode_review(pr, tmp_path, 120))
    assert result.reviewer == "opencode"
    assert result.status == "ok"
    assert "### Findings" in result.markdown


def test_run_opencode_review_error(monkeypatch, tmp_path: Path) -> None:
    pr = _sample_pr()

    async def fake_run(args, cwd, timeout):
        return (1, "", "model not found")

    monkeypatch.setattr("code_reviewer.reviewers.opencode_cli.run_command_async", fake_run)

    result = asyncio.run(run_opencode_review(pr, tmp_path, 120))
    assert result.reviewer == "opencode"
    assert result.status == "error"
    assert result.error is not None


def test_run_opencode_review_timeout(monkeypatch, tmp_path: Path) -> None:
    pr = _sample_pr()

    async def fake_run(args, cwd, timeout):
        raise TimeoutError()

    monkeypatch.setattr("code_reviewer.reviewers.opencode_cli.run_command_async", fake_run)

    result = asyncio.run(run_opencode_review(pr, tmp_path, 120))
    assert result.reviewer == "opencode"
    assert result.status == "error"
    assert "timed out" in result.error
