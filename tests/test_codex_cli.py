import asyncio
from pathlib import Path

from code_reviewer.models import PRCandidate
from code_reviewer.reviewers.codex_cli import (
    _build_codex_exec_command,
    _codex_review_json_unsupported,
    _extract_codex_markdown_from_jsonl,
    _extract_codex_review_text,
    _parse_codex_jsonl,
    _sanitize_codex_markdown,
    run_codex_review,
)


def test_extract_codex_review_from_stdout() -> None:
    out = "### Findings\n- [P2] file.rs:10 - issue"
    err = "logs"
    assert _extract_codex_review_text(out, err) == out


def test_extract_codex_review_from_stderr_codex_marker() -> None:
    err = "line1\nline2\ncodex\n### Findings\n- No material findings."
    assert _extract_codex_review_text("", err) == "### Findings\n- No material findings."


def test_extract_codex_review_from_stderr_assistant_marker() -> None:
    err = "foo\nassistant\n### Findings\n- [P3] src/lib.rs:1 - nit."
    assert _extract_codex_review_text("", err) == "### Findings\n- [P3] src/lib.rs:1 - nit."


def test_sanitize_codex_markdown_removes_internal_warnings() -> None:
    text = (
        "I found one issue.\n"
        'Failed to write last message file "/tmp/x": No such file or directory (os error 2)\n'
        "Warning: no last agent message; wrote empty content to /tmp/x\n"
    )
    assert _sanitize_codex_markdown(text) == "I found one issue."


def test_build_codex_exec_command_includes_model_and_reasoning(tmp_path) -> None:
    args = _build_codex_exec_command(
        "Say hello",
        model="gpt-5.3-codex",
        reasoning_effort="medium",
        output_last_message_path=tmp_path / "last.md",
    )

    assert args[:3] == ["codex", "exec", "--json"]
    assert "--skip-git-repo-check" in args
    assert "--output-last-message" in args
    assert str(tmp_path / "last.md") in args
    assert "Say hello" in args
    assert "-m" in args
    assert "gpt-5.3-codex" in args
    assert 'model_reasoning_effort="medium"' in args


def test_build_codex_exec_command_without_json(tmp_path) -> None:
    args = _build_codex_exec_command(
        "Say hello",
        model=None,
        reasoning_effort=None,
        output_last_message_path=tmp_path / "last.md",
        use_json=False,
    )

    assert "--json" not in args
    assert args[:2] == ["codex", "exec"]


def test_codex_review_json_unsupported_detection() -> None:
    assert _codex_review_json_unsupported("error: unexpected argument '--json' found")
    assert not _codex_review_json_unsupported("some other error")


def test_extract_codex_markdown_from_jsonl_uses_last_agent_message() -> None:
    stream = "\n".join(
        [
            "2026-03-02T00:00:00Z WARN something",
            '{"type":"thread.started","thread_id":"abc"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"second"}}',
        ]
    )

    markdown, event_count = _extract_codex_markdown_from_jsonl(stream)

    assert markdown == "second"
    assert event_count == 3


def test_run_codex_review_uses_prompt_execution(monkeypatch, tmp_path: Path) -> None:
    pr = PRCandidate(
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
    captured: dict[str, object] = {}

    async def fake_run_codex_prompt(
        prompt, workspace, timeout_seconds, *, model=None, reasoning_effort=None
    ):  # noqa: ANN001
        captured["prompt"] = prompt
        captured["workspace"] = workspace
        captured["timeout_seconds"] = timeout_seconds
        captured["model"] = model
        captured["reasoning_effort"] = reasoning_effort
        md = "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."
        return md, [{"type": "test"}]

    monkeypatch.setattr("code_reviewer.reviewers.codex_cli.run_codex_prompt", fake_run_codex_prompt)

    result = asyncio.run(
        run_codex_review(
            pr,
            tmp_path,
            45,
            model="gpt-5.3-codex",
            reasoning_effort="high",
        )
    )

    assert result.status == "ok"
    assert result.conversation == [{"type": "test"}]
    assert captured["workspace"] == tmp_path
    assert captured["timeout_seconds"] == 45
    assert captured["model"] == "gpt-5.3-codex"
    assert captured["reasoning_effort"] == "high"
    assert "actionable bugs" in str(captured["prompt"])


def test_parse_codex_jsonl_extracts_events() -> None:
    raw = "\n".join(
        [
            '{"type":"thread.started","thread_id":"abc"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"hello"}}',
            "not valid json",
            '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":50}}',
        ]
    )

    events = _parse_codex_jsonl(raw)

    assert len(events) == 3
    assert events[0]["type"] == "thread.started"
    assert events[1]["type"] == "item.completed"
    assert events[2]["type"] == "turn.completed"


def test_parse_codex_jsonl_empty() -> None:
    assert _parse_codex_jsonl("") == []
    assert _parse_codex_jsonl("\n\n") == []
