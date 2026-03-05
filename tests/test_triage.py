import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pr_reviewer.models import PRCandidate
from pr_reviewer.reviewers.triage import run_triage, TriageResult


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


def test_triage_returns_simple_when_model_says_simple(tmp_path: Path) -> None:
    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        return '{"classification": "simple"}', None

    with patch("pr_reviewer.reviewers.triage._run_claude_prompt", side_effect=fake_claude_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="claude")
        )
    assert result == TriageResult.SIMPLE


def test_triage_returns_full_review_when_model_says_full(tmp_path: Path) -> None:
    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        return '{"classification": "full_review"}', None

    with patch("pr_reviewer.reviewers.triage._run_claude_prompt", side_effect=fake_claude_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="claude")
        )
    assert result == TriageResult.FULL_REVIEW


def test_triage_falls_back_to_full_review_on_parse_error(tmp_path: Path) -> None:
    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        return "not valid json", None

    with patch("pr_reviewer.reviewers.triage._run_claude_prompt", side_effect=fake_claude_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="claude")
        )
    assert result == TriageResult.FULL_REVIEW


def test_triage_falls_back_to_full_review_on_exception(tmp_path: Path) -> None:
    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        raise RuntimeError("timeout")

    with patch("pr_reviewer.reviewers.triage._run_claude_prompt", side_effect=fake_claude_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="claude")
        )
    assert result == TriageResult.FULL_REVIEW


def test_triage_gemini_backend(tmp_path: Path) -> None:
    async def fake_gemini_prompt(prompt, cwd, timeout, **kwargs):
        return '{"classification": "simple"}'

    with patch("pr_reviewer.reviewers.triage.run_gemini_prompt", side_effect=fake_gemini_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="gemini")
        )
    assert result == TriageResult.SIMPLE


def test_triage_codex_backend(tmp_path: Path) -> None:
    async def fake_codex_prompt(prompt, cwd, timeout, **kwargs):
        return '{"classification": "simple"}'

    with patch("pr_reviewer.reviewers.triage.run_codex_prompt", side_effect=fake_codex_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="codex")
        )
    assert result == TriageResult.SIMPLE


def test_triage_extracts_json_from_markdown_code_block(tmp_path: Path) -> None:
    async def fake_claude_prompt(prompt, cwd, timeout, **kwargs):
        return '```json\n{"classification": "simple"}\n```', None

    with patch("pr_reviewer.reviewers.triage._run_claude_prompt", side_effect=fake_claude_prompt):
        result = asyncio.run(
            run_triage(_sample_pr(), tmp_path, timeout_seconds=60, backend="claude")
        )
    assert result == TriageResult.SIMPLE
