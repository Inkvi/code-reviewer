import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from code_reviewer.local_review import (
    build_local_candidate,
    current_branch,
    gather_diff_metadata,
    resolve_diff_refs,
    resolve_head_sha,
    validate_git_repo,
)
from code_reviewer.models import ReviewerOutput
from code_reviewer.processor import process_local_review


def _init_git_repo(tmp_path: Path) -> Path:
    """Create a git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "commit.gpgsign", "false"],
        capture_output=True,
        check=True,
    )
    (repo / "README.md").write_text("# hello\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"],
        capture_output=True,
        check=True,
    )
    return repo


def _add_branch_with_changes(repo: Path, branch: str, filename: str, content: str) -> str:
    """Create a branch with a file change and return its head SHA."""
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", branch],
        capture_output=True,
        check=True,
    )
    (repo / filename).write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", f"add {filename}"],
        capture_output=True,
        check=True,
    )
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# --- validate_git_repo ---


def test_validate_git_repo_valid(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    validate_git_repo(repo)  # should not raise


def test_validate_git_repo_invalid(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Not a git repository"):
        validate_git_repo(tmp_path)


# --- resolve_head_sha ---


def test_resolve_head_sha(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    sha = resolve_head_sha(repo, "HEAD")
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


# --- current_branch ---


def test_current_branch(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    branch = current_branch(repo)
    assert branch is not None
    assert len(branch) > 0


# --- resolve_diff_refs ---


def test_resolve_diff_refs_branch_mode(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _add_branch_with_changes(repo, "feature", "app.py", "print('hello')\n")

    base_ref, head_ref = resolve_diff_refs(repo, mode="branch", base="main", branch="feature")
    assert base_ref == "main"
    assert head_ref == "feature"


def test_resolve_diff_refs_branch_mode_requires_base(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    with pytest.raises(ValueError, match="--base is required"):
        resolve_diff_refs(repo, mode="branch", base=None)


def test_resolve_diff_refs_uncommitted(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    base_ref, head_ref = resolve_diff_refs(repo, mode="uncommitted")
    assert base_ref == "HEAD"
    assert head_ref == "WORKING_TREE"


def test_resolve_diff_refs_commit_mode(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    sha = _add_branch_with_changes(repo, "feature", "app.py", "code\n")
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "feature"],
        capture_output=True,
        check=True,
    )

    base_ref, head_ref = resolve_diff_refs(repo, mode="commit", commit=sha)
    assert base_ref == f"{sha}~1"
    assert head_ref == sha


def test_resolve_diff_refs_commit_requires_sha(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    with pytest.raises(ValueError, match="--commit is required"):
        resolve_diff_refs(repo, mode="commit", commit=None)


def test_resolve_diff_refs_unknown_mode(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    with pytest.raises(ValueError, match="Unknown review mode"):
        resolve_diff_refs(repo, mode="invalid")


# --- gather_diff_metadata ---


def test_gather_diff_metadata_branch(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    _add_branch_with_changes(repo, "feature", "app.py", "print('hello')\n")

    adds, dels, files = gather_diff_metadata(repo, "main", "feature")
    assert adds >= 1
    assert dels >= 0
    assert "app.py" in files


def test_gather_diff_metadata_uncommitted(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    # Modify an existing tracked file so git diff HEAD shows it
    (repo / "README.md").write_text("# hello\nmodified line\n")

    adds, dels, files = gather_diff_metadata(repo, "HEAD", "WORKING_TREE")
    assert adds >= 1
    assert "README.md" in files


# --- build_local_candidate ---


def test_build_local_candidate_branch(tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    candidate = build_local_candidate(
        repo,
        mode="branch",
        base_ref="main",
        head_ref="feature",
        head_sha="abc123def456",
        additions=10,
        deletions=3,
        changed_file_paths=["src/app.py"],
    )
    assert candidate.is_local is True
    assert candidate.owner == "local"
    assert candidate.repo.startswith("myrepo-")
    assert candidate.number == 0
    assert candidate.base_ref == "main"
    assert candidate.head_sha == "abc123def456"
    assert "feature" in candidate.title
    assert "main" in candidate.title
    assert candidate.additions == 10
    assert candidate.deletions == 3
    assert candidate.changed_file_paths == ["src/app.py"]


def test_build_local_candidate_uncommitted(tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    candidate = build_local_candidate(
        repo,
        mode="uncommitted",
        base_ref="HEAD",
        head_ref="WORKING_TREE",
        head_sha="deadbeef1234",
        additions=5,
        deletions=1,
        changed_file_paths=["config.yaml"],
    )
    assert candidate.is_local is True
    assert "Uncommitted" in candidate.title


def test_build_local_candidate_commit(tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    candidate = build_local_candidate(
        repo,
        mode="commit",
        base_ref="abc123~1",
        head_ref="abc123",
        head_sha="abc123456789",
        additions=2,
        deletions=0,
        changed_file_paths=["fix.py"],
    )
    assert candidate.is_local is True
    assert "abc123456789"[:12] in candidate.title


# --- process_local_review (mocked reviewers) ---


def _make_config(**overrides: object) -> object:
    """Build a minimal AppConfig-like object for testing."""
    from code_reviewer.config import AppConfig

    defaults = {
        "github_orgs": ["test-org"],
        "enabled_reviewers": ["claude"],
        "output_dir": "/tmp/test-reviews",
    }
    defaults.update(overrides)
    return AppConfig.model_validate(defaults)


def _ok_reviewer_output(name: str) -> ReviewerOutput:
    now = datetime.now(UTC)
    return ReviewerOutput(
        reviewer=name,
        status="ok",
        markdown="### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )


def test_process_local_review_runs_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify process_local_review runs triage + review and returns a result."""
    repo = _init_git_repo(tmp_path)
    _add_branch_with_changes(repo, "feature", "app.py", "print('hello')\n")

    config = _make_config(
        enabled_reviewers=["claude"],
        output_dir=str(tmp_path / "reviews"),
    )

    candidate = build_local_candidate(
        repo,
        mode="branch",
        base_ref="main",
        head_ref="feature",
        head_sha="deadbeef1234" * 4,
        additions=1,
        deletions=0,
        changed_file_paths=["app.py"],
    )

    # Mock triage to return FULL_REVIEW
    from code_reviewer.reviewers import triage

    async def mock_triage(*args, **kwargs):  # noqa: ANN002, ANN003
        return triage.TriageResult.FULL_REVIEW

    monkeypatch.setattr("code_reviewer.processor.run_triage", mock_triage)

    # Mock Claude review
    async def mock_claude_review(*args, **kwargs):  # noqa: ANN002, ANN003
        return _ok_reviewer_output("claude")

    monkeypatch.setattr("code_reviewer.processor.run_claude_review", mock_claude_review)

    result = asyncio.run(process_local_review(config, candidate, repo))
    assert result.processed is True
    assert result.status == "generated"
    assert result.final_review is not None
    assert "### Findings" in result.final_review


def test_process_local_review_lightweight_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify process_local_review handles lightweight (simple) triage path."""
    repo = _init_git_repo(tmp_path)

    config = _make_config(
        enabled_reviewers=["claude"],
        output_dir=str(tmp_path / "reviews"),
    )

    candidate = build_local_candidate(
        repo,
        mode="uncommitted",
        base_ref="HEAD",
        head_ref="WORKING_TREE",
        head_sha="abc1234567890000",
        additions=1,
        deletions=0,
        changed_file_paths=["config.yaml"],
    )

    from code_reviewer.reviewers import triage

    async def mock_triage(*args, **kwargs):  # noqa: ANN002, ANN003
        return triage.TriageResult.SIMPLE

    monkeypatch.setattr("code_reviewer.processor.run_triage", mock_triage)

    async def mock_lightweight(*args, **kwargs):  # noqa: ANN002, ANN003
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.", None

    monkeypatch.setattr("code_reviewer.processor.run_lightweight_review", mock_lightweight)

    result = asyncio.run(process_local_review(config, candidate, repo))
    assert result.processed is True
    assert result.status == "lightweight_generated"
    assert result.triage_result == "simple"


def test_gather_diff_metadata_uncommitted_includes_untracked(tmp_path: Path) -> None:
    """Verify untracked files are included in uncommitted diff metadata."""
    repo = _init_git_repo(tmp_path)
    # Create an untracked file (not staged or committed)
    (repo / "newfile.py").write_text("line1\nline2\nline3\n")

    adds, dels, files = gather_diff_metadata(repo, "HEAD", "WORKING_TREE")
    assert "newfile.py" in files
    assert adds >= 3  # 3 lines in the untracked file


def test_process_local_review_reconciler_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify process_local_review runs reconciler when 2+ reviewers enabled."""
    repo = _init_git_repo(tmp_path)

    config = _make_config(
        enabled_reviewers=["claude", "gemini"],
        output_dir=str(tmp_path / "reviews"),
    )

    candidate = build_local_candidate(
        repo,
        mode="branch",
        base_ref="main",
        head_ref="feature",
        head_sha="deadbeef1234" * 4,
        additions=1,
        deletions=0,
        changed_file_paths=["app.py"],
    )

    from code_reviewer.reviewers import triage

    async def mock_triage(*args, **kwargs):  # noqa: ANN002, ANN003
        return triage.TriageResult.FULL_REVIEW

    monkeypatch.setattr("code_reviewer.processor.run_triage", mock_triage)

    async def mock_claude_review(*args, **kwargs):  # noqa: ANN002, ANN003
        return _ok_reviewer_output("claude")

    async def mock_gemini_review(*args, **kwargs):  # noqa: ANN002, ANN003
        return _ok_reviewer_output("gemini")

    async def mock_reconcile(*args, **kwargs):  # noqa: ANN002, ANN003
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.", None

    monkeypatch.setattr("code_reviewer.processor.run_claude_review", mock_claude_review)
    monkeypatch.setattr("code_reviewer.processor.run_gemini_review", mock_gemini_review)
    monkeypatch.setattr("code_reviewer.processor.reconcile_reviews", mock_reconcile)

    result = asyncio.run(process_local_review(config, candidate, repo))
    assert result.processed is True
    assert result.status == "generated"
    assert result.triage_result == "full_review"


def test_process_local_review_error_handling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify process_local_review returns error result on exception."""
    repo = _init_git_repo(tmp_path)

    config = _make_config(
        enabled_reviewers=["claude"],
        output_dir=str(tmp_path / "reviews"),
    )

    candidate = build_local_candidate(
        repo,
        mode="branch",
        base_ref="main",
        head_ref="feature",
        head_sha="deadbeef1234" * 4,
        additions=1,
        deletions=0,
        changed_file_paths=["app.py"],
    )

    async def mock_triage(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("triage exploded")

    monkeypatch.setattr("code_reviewer.processor.run_triage", mock_triage)

    result = asyncio.run(process_local_review(config, candidate, repo))
    assert result.processed is False
    assert result.status == "error"
    assert "triage exploded" in result.error


def test_build_local_candidate_sets_review_mode(tmp_path: Path) -> None:
    """Verify build_local_candidate stores review_mode on the candidate."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    candidate = build_local_candidate(
        repo,
        mode="uncommitted",
        base_ref="HEAD",
        head_ref="WORKING_TREE",
        head_sha="deadbeef1234",
        additions=1,
        deletions=0,
        changed_file_paths=["file.py"],
    )
    assert candidate.review_mode == "uncommitted"
