from pathlib import Path

from code_reviewer.models import PRCandidate
from code_reviewer.output import write_review_markdown, write_stage_markdown


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


def test_write_stage_markdown(tmp_path: Path) -> None:
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
    content = "### Claude Review\n- looks good"
    path = write_stage_markdown(tmp_path, pr, "claude", content)

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text == f"{content}\n"
    assert path == tmp_path / "org" / "repo" / "pr-42.claude.md"
    versioned_dir = tmp_path / "org" / "repo" / "pr-42"
    versioned_paths = list(versioned_dir.glob("*.claude.md"))
    assert len(versioned_paths) == 1
    assert versioned_paths[0].read_text(encoding="utf-8") == text


def test_write_stage_markdown_reconcile(tmp_path: Path) -> None:
    pr = PRCandidate(
        owner="org",
        repo="repo",
        number=7,
        url="https://example.com/pr/7",
        title="Add feature",
        author_login="bob",
        base_ref="main",
        head_sha="abc123",
        updated_at="2026-01-01T00:00:00Z",
    )
    content = "### Reconciled Review\n- merged findings"
    path = write_stage_markdown(tmp_path, pr, "reconcile", content)

    assert path == tmp_path / "org" / "repo" / "pr-7.reconcile.md"
    assert path.read_text(encoding="utf-8") == f"{content}\n"
    versioned_paths = list((tmp_path / "org" / "repo" / "pr-7").glob("*.reconcile.md"))
    assert len(versioned_paths) == 1
