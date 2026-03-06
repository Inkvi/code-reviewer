"""Extra processor tests covering edge cases and untested paths."""
import asyncio
from datetime import UTC, datetime

from code_reviewer.config import AppConfig
from code_reviewer.github import GitHubClient
from code_reviewer.models import PRCandidate, ProcessedState, ReviewerOutput, TokenUsage
from code_reviewer.processor import (
    _check_pr_head_changed,
    _compute_total_token_usage,
    _make_reviewer_summaries,
    _output_version_label,
    _parse_iso_timestamp,
    _validate_review_format,
    process_candidate,
)


def _sample_pr(**kwargs) -> PRCandidate:  # noqa: ANN003
    defaults = dict(
        owner="polymerdao", repo="obul", number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test", author_login="alice", base_ref="main",
        head_sha="deadbeef", updated_at="2026-02-27T20:00:00Z",
        latest_direct_rerequest_at="2026-03-02T00:00:00+00:00",
        additions=8, deletions=4, changed_file_paths=["src/app.py"],
    )
    defaults.update(kwargs)
    return PRCandidate(**defaults)


def _ok_output(name: str, *, token_usage: TokenUsage | None = None) -> ReviewerOutput:
    now = datetime.now(UTC)
    md = "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."
    return ReviewerOutput(
        reviewer=name, status="ok", markdown=md,
        stdout="", stderr="", error=None, started_at=now, ended_at=now,
        token_usage=token_usage,
    )


# --- _validate_review_format ---


def test_validate_review_format_valid() -> None:
    text = "### Findings\n- something\n\n### Test Gaps\n- None noted."
    assert _validate_review_format(text) == text


def test_validate_review_format_missing_findings() -> None:
    text = "some random text without proper sections"
    result = _validate_review_format(text)
    assert "### Findings" in result
    assert "[P0]" in result
    assert "prompt injection" in result


def test_validate_review_format_missing_test_gaps() -> None:
    text = "### Findings\n- something"
    result = _validate_review_format(text)
    assert "prompt injection" in result


# --- _parse_iso_timestamp ---


def test_parse_iso_timestamp_valid() -> None:
    result = _parse_iso_timestamp("2026-03-02T00:00:00+00:00")
    assert result is not None
    assert result.year == 2026


def test_parse_iso_timestamp_with_z() -> None:
    result = _parse_iso_timestamp("2026-03-02T00:00:00Z")
    assert result is not None


def test_parse_iso_timestamp_none() -> None:
    assert _parse_iso_timestamp(None) is None


def test_parse_iso_timestamp_empty() -> None:
    assert _parse_iso_timestamp("") is None
    assert _parse_iso_timestamp("   ") is None


def test_parse_iso_timestamp_invalid() -> None:
    assert _parse_iso_timestamp("not-a-date") is None


# --- _output_version_label ---


def test_output_version_label() -> None:
    pr = _sample_pr()
    label = _output_version_label(pr)
    assert "deadbeef" in label
    assert "T" in label


def test_output_version_label_no_head_sha() -> None:
    pr = _sample_pr(head_sha="")
    label = _output_version_label(pr)
    assert "nohead" in label


# --- _check_pr_head_changed ---


def test_check_pr_head_changed_no_change(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    pr = _sample_pr()
    monkeypatch.setattr(
        "code_reviewer.processor.GitHubClient.get_pr_head_sha",
        staticmethod(lambda _pr: "deadbeef"),
    )
    result = _check_pr_head_changed(client, pr)
    assert result is None


def test_check_pr_head_changed_new_commit(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    pr = _sample_pr()
    monkeypatch.setattr(
        "code_reviewer.processor.GitHubClient.get_pr_head_sha",
        staticmethod(lambda _pr: "newsha123"),
    )
    result = _check_pr_head_changed(client, pr)
    assert result == "newsha123"


def test_check_pr_head_changed_error_returns_none(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    pr = _sample_pr()
    monkeypatch.setattr(
        "code_reviewer.processor.GitHubClient.get_pr_head_sha",
        staticmethod(lambda _pr: (_ for _ in ()).throw(RuntimeError("network error"))),
    )
    warnings: list[str] = []
    monkeypatch.setattr("code_reviewer.processor.warn", warnings.append)
    result = _check_pr_head_changed(client, pr)
    assert result is None
    assert any("failed to poll" in w for w in warnings)


# --- _make_reviewer_summaries ---


def test_make_reviewer_summaries() -> None:
    outputs = {
        "claude": _ok_output("claude", token_usage=TokenUsage(100, 50, 0.01)),
        "codex": _ok_output("codex"),
    }
    summaries = _make_reviewer_summaries(outputs)
    assert len(summaries) == 2
    assert summaries[0].reviewer == "claude"
    assert summaries[0].token_usage is not None
    assert summaries[1].token_usage is None


# --- _compute_total_token_usage ---


def test_compute_total_token_usage() -> None:
    outputs = {
        "claude": _ok_output("claude", token_usage=TokenUsage(100, 50)),
        "codex": _ok_output("codex", token_usage=TokenUsage(200, 100)),
    }
    reconciler = TokenUsage(50, 25)
    total = _compute_total_token_usage(outputs, reconciler)
    assert total is not None
    assert total.input_tokens == 350
    assert total.output_tokens == 175


def test_compute_total_token_usage_all_none() -> None:
    outputs = {"claude": _ok_output("claude")}
    total = _compute_total_token_usage(outputs, None)
    assert total is None


# --- process_candidate: skip own PR ---


def test_process_candidate_skips_own_pr(monkeypatch) -> None:
    config = AppConfig(github_orgs=["polymerdao"])
    client = GitHubClient(viewer_login="alice")
    pr = _sample_pr(author_login="alice")

    class DummyStore:
        def get(self, _k):  # noqa: ANN001
            return ProcessedState()
        def set(self, _k, _s):  # noqa: ANN001
            pass
        def save(self):  # noqa: ANN001
            pass

    result = asyncio.run(process_candidate(config, client, DummyStore(), object(), pr))
    assert result.processed is False
    assert result.status == "skipped_own_pr"


# --- process_candidate: error handling ---


def test_process_candidate_workspace_error(monkeypatch) -> None:
    config = AppConfig(github_orgs=["polymerdao"])
    client = GitHubClient(viewer_login="Inkvi")
    pr = _sample_pr()

    class DummyStore:
        def get(self, _k):  # noqa: ANN001
            return ProcessedState()
        def set(self, _k, _s):  # noqa: ANN001
            pass
        def save(self):  # noqa: ANN001
            pass

    class FailWorkspace:
        def prepare(self, _pr):  # noqa: ANN001
            raise RuntimeError("clone failed")
        def cleanup(self, _workdir):  # noqa: ANN001
            pass

    monkeypatch.setattr("code_reviewer.processor.info", lambda *a: None)
    monkeypatch.setattr("code_reviewer.processor.warn", lambda *a: None)

    result = asyncio.run(process_candidate(config, client, DummyStore(), FailWorkspace(), pr))
    assert result.processed is False
    assert result.status == "error"
    assert "clone failed" in result.error
