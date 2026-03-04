from pr_reviewer.models import PRCandidate
from pr_reviewer.reviewers.gemini_cli import (
    _build_gemini_review_command,
    _extract_gemini_review_text,
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
    assert "--approval-mode" in args
    approval_idx = args.index("--approval-mode")
    assert args[approval_idx + 1] == "yolo"
    assert "--output-format" in args
    output_format_idx = args.index("--output-format")
    assert args[output_format_idx + 1] == "json"
    assert "-m" not in args


def test_build_gemini_review_command_with_model() -> None:
    pr = _sample_pr()
    args = _build_gemini_review_command(pr, model="gemini-3.1-pro-preview")

    assert "-m" in args
    model_idx = args.index("-m")
    assert args[model_idx + 1] == "gemini-3.1-pro-preview"


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
