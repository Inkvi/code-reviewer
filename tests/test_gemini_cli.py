import asyncio
from pathlib import Path

from code_reviewer.models import PRCandidate
from code_reviewer.reviewers.gemini_cli import (
    _build_gemini_prompt_command,
    _build_gemini_review_command,
    _extract_gemini_review_text,
    run_gemini_review,
)


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


def test_build_gemini_review_command_without_model() -> None:
    pr = _sample_pr()
    args = _build_gemini_review_command(pr, model=None)

    assert args[0] == "gemini"
    assert "-p" in args
    prompt_idx = args.index("-p")
    assert args[prompt_idx + 1] == "/code-review"
    assert "-e" in args
    extension_idx = args.index("-e")
    assert args[extension_idx + 1] == "code-review"
    assert "-m" not in args


def test_build_gemini_prompt_command_with_model() -> None:
    args = _build_gemini_prompt_command(
        "Summarize findings",
        model="gemini-3.1-pro-preview",
    )

    assert args[0] == "gemini"
    assert "-p" in args
    prompt_idx = args.index("-p")
    assert args[prompt_idx + 1] == "Summarize findings"
    assert "--approval-mode" in args
    approval_idx = args.index("--approval-mode")
    assert args[approval_idx + 1] == "yolo"
    assert "--output-format" in args
    output_format_idx = args.index("--output-format")
    assert args[output_format_idx + 1] == "json"
    assert "-m" in args


def test_extract_gemini_review_text_from_stdout() -> None:
    stdout = "### Findings\n- [P2] file.rs:10 - issue"
    stderr = "logs"

    result = _extract_gemini_review_text(stdout, stderr)
    assert result == stdout


def test_extract_gemini_review_text_from_json() -> None:
    stdout = '{"response": "### Findings\\n- No material findings."}'
    stderr = ""

    result = _extract_gemini_review_text(stdout, stderr)
    assert "No material findings" in result


def test_extract_gemini_review_text_from_multiline_json() -> None:
    stdout = (
        "Loaded cached credentials.\n"
        '{\n  "session_id": "abc",\n'
        '  "response": "### Findings\\n- No material findings."\n}\n'
    )
    stderr = ""

    result = _extract_gemini_review_text(stdout, stderr)
    assert "No material findings" in result


def test_extract_gemini_review_text_from_json_with_parts() -> None:
    stdout = '{"parts": [{"text": "gemini review content"}]}'
    stderr = ""

    result = _extract_gemini_review_text(stdout, stderr)
    assert result == "gemini review content"


def test_extract_gemini_review_text_joins_json_parts() -> None:
    stdout = '{"parts": [{"text": "part one"}, {"text": "part two"}]}'
    stderr = ""

    result = _extract_gemini_review_text(stdout, stderr)
    assert result == "part one\npart two"


def test_extract_gemini_review_text_empty() -> None:
    result = _extract_gemini_review_text("", "")
    assert result == ""


def test_extract_gemini_review_text_falls_back_to_stderr_marker() -> None:
    stdout = ""
    stderr = "some logs\ngemini\n### Findings\n- [P3] note."

    result = _extract_gemini_review_text(stdout, stderr)
    assert "### Findings" in result
    assert "[P3]" in result


def test_run_gemini_review_uses_extension_by_default(monkeypatch, tmp_path: Path) -> None:
    pr = _sample_pr()
    captured: dict[str, object] = {}

    async def fake_run_command_async(args, cwd, timeout):  # noqa: ANN001
        captured["args"] = args
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        return (
            0,
            "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.",
            "",
        )

    monkeypatch.setattr(
        "code_reviewer.reviewers.gemini_cli.run_command_async",
        fake_run_command_async,
    )

    result = asyncio.run(run_gemini_review(pr, tmp_path, 45, model="gemini-3.1-pro-preview"))

    assert result.status == "ok"
    assert captured["cwd"] == tmp_path
    assert captured["timeout"] == 45
    assert "-e" in captured["args"]
    assert "code-review" in captured["args"]


def test_run_gemini_review_uses_prompt_execution_when_override_set(
    monkeypatch, tmp_path: Path
) -> None:
    pr = _sample_pr()
    captured: dict[str, object] = {}
    prompt_path = tmp_path / "full.toml"
    prompt_path.write_text('prompt = "Review {url}\\nRun {diff_command}"\n', encoding="utf-8")

    async def fake_run_gemini_prompt(prompt, workspace, timeout_seconds, *, model=None):  # noqa: ANN001
        captured["prompt"] = prompt
        captured["workspace"] = workspace
        captured["timeout_seconds"] = timeout_seconds
        captured["model"] = model
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."

    monkeypatch.setattr(
        "code_reviewer.reviewers.gemini_cli.run_gemini_prompt",
        fake_run_gemini_prompt,
    )

    result = asyncio.run(
        run_gemini_review(
            pr,
            tmp_path,
            45,
            model="gemini-3.1-pro-preview",
            prompt_path=str(prompt_path),
        )
    )

    assert result.status == "ok"
    assert captured["workspace"] == tmp_path
    assert captured["timeout_seconds"] == 45
    assert captured["model"] == "gemini-3.1-pro-preview"
    assert "git diff origin/main...HEAD" in str(captured["prompt"])
