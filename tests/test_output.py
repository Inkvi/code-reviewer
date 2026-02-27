from datetime import UTC, datetime
from pathlib import Path

from pr_reviewer.models import PRCandidate, ReviewerOutput
from pr_reviewer.output import write_review_markdown


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
    now = datetime.now(UTC)
    claude = ReviewerOutput("claude", "ok", "C", "C", "", None, now, now)
    codex = ReviewerOutput("codex", "ok", "D", "D", "", None, now, now)

    path = write_review_markdown(tmp_path, pr, "final", claude, codex)

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Final Reconciled Review" in text
    assert "pr-42.md" in str(path)
