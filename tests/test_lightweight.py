import asyncio
from pathlib import Path
from unittest.mock import patch

from pr_reviewer.models import PRCandidate, TokenUsage
from pr_reviewer.reviewers.lightweight import run_lightweight_review


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="bump redis image to 7.2",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
        additions=3,
        deletions=1,
        changed_file_paths=["docker-compose.yaml"],
    )


def test_lightweight_review_claude_returns_formatted_output(tmp_path: Path) -> None:
    review_text = "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."
    token_usage = TokenUsage(input_tokens=100, output_tokens=50, cost_usd=0.001)

    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        return review_text, token_usage

    with patch(
        "pr_reviewer.reviewers.lightweight._run_claude_prompt",
        side_effect=fake_claude_prompt,
    ):
        text, usage = asyncio.run(
            run_lightweight_review(
                _sample_pr(), tmp_path, timeout_seconds=300, backend="claude"
            )
        )

    assert "### Findings" in text
    assert "### Test Gaps" in text
    assert usage == token_usage


def test_lightweight_review_gemini_backend(tmp_path: Path) -> None:
    review_text = "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."

    async def fake_gemini_prompt(prompt, cwd, timeout, **kwargs):
        return review_text

    with patch(
        "pr_reviewer.reviewers.lightweight.run_gemini_prompt",
        side_effect=fake_gemini_prompt,
    ):
        text, usage = asyncio.run(
            run_lightweight_review(
                _sample_pr(), tmp_path, timeout_seconds=300, backend="gemini"
            )
        )

    assert "### Findings" in text
    assert usage is None


def test_lightweight_review_codex_backend(tmp_path: Path) -> None:
    review_text = "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."

    async def fake_codex_prompt(prompt, cwd, timeout, **kwargs):
        return review_text

    with patch(
        "pr_reviewer.reviewers.lightweight.run_codex_prompt",
        side_effect=fake_codex_prompt,
    ):
        text, usage = asyncio.run(
            run_lightweight_review(
                _sample_pr(), tmp_path, timeout_seconds=300, backend="codex"
            )
        )

    assert "### Findings" in text
    assert usage is None


def test_lightweight_review_prompt_contains_checklist_items(tmp_path: Path) -> None:
    captured_prompts: list[str] = []

    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        captured_prompts.append(prompt)
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.", None

    with patch(
        "pr_reviewer.reviewers.lightweight._run_claude_prompt",
        side_effect=fake_claude_prompt,
    ):
        asyncio.run(
            run_lightweight_review(
                _sample_pr(), tmp_path, timeout_seconds=300, backend="claude"
            )
        )

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0].lower()
    assert "syntax" in prompt or "well-formed" in prompt
    assert "secret" in prompt
    assert "breaking" in prompt
