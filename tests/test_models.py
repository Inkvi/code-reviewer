from datetime import UTC, datetime, timedelta

import pytest

from code_reviewer.models import (
    PRCandidate,
    ProcessedState,
    ProcessingResult,
    ReviewerOutput,
    ReviewerOutputSummary,
    TokenUsage,
)


def test_token_usage_add_both_have_cost() -> None:
    a = TokenUsage(input_tokens=100, output_tokens=50, cost_usd=0.01)
    b = TokenUsage(input_tokens=200, output_tokens=100, cost_usd=0.02)
    result = a + b
    assert result.input_tokens == 300
    assert result.output_tokens == 150
    assert result.cost_usd == pytest.approx(0.03)


def test_token_usage_add_one_has_cost() -> None:
    a = TokenUsage(input_tokens=100, output_tokens=50, cost_usd=0.01)
    b = TokenUsage(input_tokens=200, output_tokens=100, cost_usd=None)
    result = a + b
    assert result.input_tokens == 300
    assert result.output_tokens == 150
    assert result.cost_usd == pytest.approx(0.01)


def test_token_usage_add_neither_has_cost() -> None:
    a = TokenUsage(input_tokens=100, output_tokens=50)
    b = TokenUsage(input_tokens=200, output_tokens=100)
    result = a + b
    assert result.input_tokens == 300
    assert result.output_tokens == 150
    assert result.cost_usd is None


def test_reviewer_output_duration_seconds() -> None:
    start = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=45)
    output = ReviewerOutput(
        reviewer="claude", status="ok", markdown="ok", stdout="", stderr="",
        error=None, started_at=start, ended_at=end,
    )
    assert output.duration_seconds == 45.0


def test_pr_candidate_key() -> None:
    pr = PRCandidate(
        owner="polymerdao", repo="obul", number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test", author_login="alice", base_ref="main",
        head_sha="deadbeef", updated_at="2026-02-27T20:00:00Z",
    )
    assert pr.key == "polymerdao/obul#64"


def test_processing_result_to_dict_minimal() -> None:
    result = ProcessingResult(
        processed=True, pr_url="https://example.com/pr/1",
        pr_key="org/repo#1", status="generated",
    )
    d = result.to_dict()
    assert d["processed"] is True
    assert d["pr_url"] == "https://example.com/pr/1"
    assert d["status"] == "generated"
    assert "final_review" not in d
    assert "error" not in d


def test_processing_result_to_dict_full() -> None:
    result = ProcessingResult(
        processed=True, pr_url="https://example.com/pr/1",
        pr_key="org/repo#1", status="generated",
        final_review="### Findings\n- No material findings.",
        output_file="/tmp/review.md",
        triage_result="full_review",
        review_decision="approve",
        reviewer_outputs=[
            ReviewerOutputSummary(
                reviewer="claude", status="ok", duration_seconds=10.0,
                token_usage=TokenUsage(input_tokens=500, output_tokens=200, cost_usd=0.005),
            ),
        ],
        total_token_usage=TokenUsage(input_tokens=500, output_tokens=200, cost_usd=0.005),
        error=None,
    )
    d = result.to_dict()
    assert d["final_review"] == "### Findings\n- No material findings."
    assert d["output_file"] == "/tmp/review.md"
    assert d["triage_result"] == "full_review"
    assert d["review_decision"] == "approve"
    assert len(d["reviewer_outputs"]) == 1
    assert d["reviewer_outputs"][0]["reviewer"] == "claude"
    assert d["reviewer_outputs"][0]["token_usage"]["input_tokens"] == 500
    assert d["total_token_usage"]["cost_usd"] == 0.005


def test_processing_result_to_dict_with_error() -> None:
    result = ProcessingResult(
        processed=False, pr_url="url", pr_key="k", status="error",
        error="something broke",
    )
    d = result.to_dict()
    assert d["error"] == "something broke"
    assert d["processed"] is False


def test_processed_state_now_iso() -> None:
    iso = ProcessedState.now_iso()
    assert "T" in iso
    assert "+" in iso or "Z" in iso
