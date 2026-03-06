from datetime import UTC, datetime
from pathlib import Path

from code_reviewer.models import PRCandidate, ReviewerOutput
from code_reviewer.output import write_review_markdown, write_reviewer_sidecar_markdown


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
    final_review = "### Findings\n- [P3] note.\n\n### Test Gaps\n- None noted."
    path = write_review_markdown(tmp_path, pr, final_review)

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text == f"{final_review}\n"
    assert "Automated Review" not in text
    assert "URL:" not in text
    assert "Base:" not in text
    assert "Head:" not in text
    assert path == tmp_path / "org" / "repo" / "pr-42.md"
    versioned_dir = tmp_path / "org" / "repo" / "pr-42"
    versioned_paths = list(versioned_dir.glob("*.md"))
    assert len(versioned_paths) == 1
    assert versioned_paths[0].read_text(encoding="utf-8") == text


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

    path = write_reviewer_sidecar_markdown(
        tmp_path, pr, {"claude": claude, "codex": codex}
    )

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Reviewer Raw Outputs" in text
    assert "claude markdown" in text
    assert "codex stderr" in text
    assert "codex failed" in text
    assert path == tmp_path / "org" / "repo" / "pr-42.raw.md"
    versioned_dir = tmp_path / "org" / "repo" / "pr-42"
    versioned_paths = list(versioned_dir.glob("*.raw.md"))
    assert len(versioned_paths) == 1
    assert versioned_paths[0].read_text(encoding="utf-8") == text


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
        tmp_path, pr, {"claude": claude, "codex": codex}, include_stderr=False
    )

    text = path.read_text(encoding="utf-8")
    assert "_omitted by config_" in text
    assert "codex stderr" not in text


def test_write_reviewer_sidecar_markdown_three_reviewers(tmp_path: Path) -> None:
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
        markdown="claude review",
        stdout="claude stdout",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )
    codex = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="codex review",
        stdout="codex stdout",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )
    gemini = ReviewerOutput(
        reviewer="gemini",
        status="ok",
        markdown="gemini review",
        stdout="gemini stdout",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    path = write_reviewer_sidecar_markdown(
        tmp_path, pr, {"claude": claude, "codex": codex, "gemini": gemini}
    )

    text = path.read_text(encoding="utf-8")
    assert "Claude" in text
    assert "Codex" in text
    assert "Gemini" in text
    assert "gemini review" in text
    # Verify ordering: Claude before Codex before Gemini
    assert text.index("Claude") < text.index("Codex") < text.index("Gemini")
