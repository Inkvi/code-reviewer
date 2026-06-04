import asyncio
from pathlib import Path

from code_reviewer.models import PRCandidate
from code_reviewer.reviewers.antigravity_cli import (
    _build_antigravity_prompt_command,
    _extract_antigravity_review_text,
    run_antigravity_review,
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


def test_build_antigravity_prompt_command_without_model() -> None:
    args = _build_antigravity_prompt_command("Summarize findings", model=None)

    assert args[0] == "agy"
    assert "-p" in args
    prompt_idx = args.index("-p")
    assert args[prompt_idx + 1] == "Summarize findings"
    assert "-o" in args
    output_idx = args.index("-o")
    assert args[output_idx + 1] == "json"
    assert "-m" not in args


def test_build_antigravity_prompt_command_with_model() -> None:
    args = _build_antigravity_prompt_command("Summarize findings", model="agy-default")

    assert args[0] == "agy"
    assert "-m" in args
    model_idx = args.index("-m")
    assert args[model_idx + 1] == "agy-default"


def test_extract_antigravity_review_text_from_stdout() -> None:
    stdout = "### Findings\n- [P2] file.rs:10 - issue"

    result = _extract_antigravity_review_text(stdout)
    assert result == stdout


def test_extract_antigravity_review_text_from_json() -> None:
    stdout = '{"response": "### Findings\\n- No material findings."}'

    result = _extract_antigravity_review_text(stdout)
    assert "No material findings" in result


def test_extract_antigravity_review_text_from_multiline_json() -> None:
    stdout = (
        "Loaded cached credentials.\n"
        '{\n  "session_id": "abc",\n'
        '  "response": "### Findings\\n- No material findings."\n}\n'
    )

    result = _extract_antigravity_review_text(stdout)
    assert "No material findings" in result


def test_extract_antigravity_review_text_from_json_with_parts() -> None:
    stdout = '{"parts": [{"text": "agy review content"}]}'

    result = _extract_antigravity_review_text(stdout)
    assert result == "agy review content"


def test_extract_antigravity_review_text_joins_json_parts() -> None:
    stdout = '{"parts": [{"text": "part one"}, {"text": "part two"}]}'

    result = _extract_antigravity_review_text(stdout)
    assert result == "part one\npart two"


def test_extract_antigravity_review_text_empty() -> None:
    result = _extract_antigravity_review_text("")
    assert result == ""


def test_run_antigravity_review_errors_without_prompt_path(tmp_path: Path) -> None:
    pr = _sample_pr()

    result = asyncio.run(run_antigravity_review(pr, tmp_path, 45, prompt_path=None))

    assert result.status == "error"
    assert "full_review_prompt_path" in (result.error or "")


def test_run_antigravity_review_uses_prompt_execution(monkeypatch, tmp_path: Path) -> None:
    pr = _sample_pr()
    captured: dict[str, object] = {}
    prompt_path = tmp_path / "full.toml"
    prompt_path.write_text('prompt = "Review {url}"\n', encoding="utf-8")

    async def fake_run_antigravity_prompt(prompt, workspace, timeout_seconds, *, model=None):  # noqa: ANN001
        captured["prompt"] = prompt
        captured["workspace"] = workspace
        captured["timeout_seconds"] = timeout_seconds
        captured["model"] = model
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."

    monkeypatch.setattr(
        "code_reviewer.reviewers.antigravity_cli.run_antigravity_prompt",
        fake_run_antigravity_prompt,
    )

    result = asyncio.run(
        run_antigravity_review(
            pr,
            tmp_path,
            45,
            model="agy-default",
            prompt_path=str(prompt_path),
        )
    )

    assert result.status == "ok"
    assert result.reviewer == "antigravity"
    assert captured["workspace"] == tmp_path
    assert captured["timeout_seconds"] == 45
    assert captured["model"] == "agy-default"
    assert "Review https://github.com/polymerdao/obul/pull/64" in str(captured["prompt"])
