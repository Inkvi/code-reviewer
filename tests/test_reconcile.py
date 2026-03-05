from datetime import UTC, datetime
from pathlib import Path

import pytest

from pr_reviewer.models import PRCandidate, ReviewerOutput
from pr_reviewer.reviewers.reconcile import reconcile_reviews


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


def _sample_output(reviewer: str) -> ReviewerOutput:
    now = datetime.now(UTC)
    return ReviewerOutput(
        reviewer=reviewer,
        status="ok",
        markdown="### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )


@pytest.mark.asyncio
async def test_reconcile_reviews_uses_codex_backend(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_codex_prompt(prompt, workspace, timeout_seconds, *, model=None, reasoning_effort=None):  # noqa: ANN001,E501
        captured["prompt"] = prompt
        captured["workspace"] = workspace
        captured["timeout_seconds"] = timeout_seconds
        captured["model"] = model
        captured["reasoning_effort"] = reasoning_effort
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."

    monkeypatch.setattr("pr_reviewer.reviewers.reconcile.run_codex_prompt", fake_codex_prompt)

    result = await reconcile_reviews(
        _sample_pr(),
        tmp_path,
        [_sample_output("claude"), _sample_output("gemini")],
        30,
        reconciler_backend="codex",
        reconciler_model="gpt-5.3-codex",
        reconciler_reasoning_effort="high",
    )

    text, usage = result
    assert "No material findings" in text
    assert usage is None
    assert captured["workspace"] == tmp_path
    assert captured["timeout_seconds"] == 30
    assert captured["model"] == "gpt-5.3-codex"
    assert captured["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_reconcile_reviews_uses_gemini_backend(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_gemini_prompt(prompt, workspace, timeout_seconds, *, model=None):  # noqa: ANN001
        captured["prompt"] = prompt
        captured["workspace"] = workspace
        captured["timeout_seconds"] = timeout_seconds
        captured["model"] = model
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."

    monkeypatch.setattr("pr_reviewer.reviewers.reconcile.run_gemini_prompt", fake_gemini_prompt)

    result = await reconcile_reviews(
        _sample_pr(),
        tmp_path,
        [_sample_output("claude"), _sample_output("codex")],
        45,
        reconciler_backend="gemini",
        reconciler_model="gemini-3.1-pro-preview",
        reconciler_reasoning_effort="high",
    )

    text, usage = result
    assert "No material findings" in text
    assert usage is None
    assert captured["workspace"] == tmp_path
    assert captured["timeout_seconds"] == 45
    assert captured["model"] == "gemini-3.1-pro-preview"


@pytest.mark.asyncio
async def test_reconcile_reviews_rejects_unknown_backend(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"Unsupported reconciler backend"):
        await reconcile_reviews(
            _sample_pr(),
            tmp_path,
            [_sample_output("claude"), _sample_output("codex")],
            45,
            reconciler_backend="other",
        )
