from datetime import UTC, datetime
from pathlib import Path

from pr_reviewer.models import PRCandidate, ReviewerOutput
from pr_reviewer.output import write_review_markdown, write_reviewer_sidecar_markdown


def test_write_review_markdown(tmp_path: Path) -> None:
    pr = PRCandidate(
        owner="org",
        repo="repo",
        number=42,
        url="https://example.com/pr/42",
        title="Fix bug",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-01-01T00:00:00Z",
    )
    path = write_review_markdown(tmp_path, pr, "final")

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Automated Review" in text
    assert "final" in text
    assert "Codex Raw Output" not in text
    assert "pr-42.md" in str(path)


def test_write_reviewer_sidecar_markdown(tmp_path: Path) -> None:
    pr = PRCandidate(
        owner="org",
        repo="repo",
        number=42,
        url="https://example.com/pr/42",
        title="Fix bug",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-01-01T00:00:00Z",
    )
    now = datetime.now(UTC)
    claude = ReviewerOutput(
        reviewer="claude",
        status="ok",
        markdown="claude markdown",
        stdout="claude stdout",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )
    codex = ReviewerOutput(
        reviewer="codex",
        status="error",
        markdown="",
        stdout="",
        stderr="codex stderr",
        error="codex failed",
        started_at=now,
        ended_at=now,
    )

    path = write_reviewer_sidecar_markdown(tmp_path, pr, claude, codex)

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Reviewer Raw Outputs" in text
    assert "claude markdown" in text
    assert "codex stderr" in text
    assert "codex failed" in text
    assert "pr-42.raw.md" in str(path)


def test_write_reviewer_sidecar_markdown_without_stderr(tmp_path: Path) -> None:
    pr = PRCandidate(
        owner="org",
        repo="repo",
        number=42,
        url="https://example.com/pr/42",
        title="Fix bug",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-01-01T00:00:00Z",
    )
    now = datetime.now(UTC)
    claude = ReviewerOutput(
        reviewer="claude",
        status="ok",
        markdown="claude markdown",
        stdout="claude stdout",
        stderr="claude stderr",
        error=None,
        started_at=now,
        ended_at=now,
    )
    codex = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="codex markdown",
        stdout="codex stdout",
        stderr="codex stderr",
        error=None,
        started_at=now,
        ended_at=now,
    )

    path = write_reviewer_sidecar_markdown(
        tmp_path, pr, claude, codex, include_stderr=False
    )

    text = path.read_text(encoding="utf-8")
    assert "_omitted by config_" in text
    assert "codex stderr" not in text
