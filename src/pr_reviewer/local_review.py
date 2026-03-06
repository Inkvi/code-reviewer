from __future__ import annotations

import subprocess
from pathlib import Path

from pr_reviewer.models import PRCandidate


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def validate_git_repo(repo: Path) -> None:
    try:
        _run_git(repo, "rev-parse", "--git-dir")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ValueError(f"Not a git repository: {repo}") from exc


def resolve_head_sha(repo: Path, ref: str) -> str:
    return _run_git(repo, "rev-parse", ref)


def current_branch(repo: Path) -> str | None:
    try:
        return _run_git(repo, "symbolic-ref", "--short", "HEAD")
    except subprocess.CalledProcessError:
        return None


def resolve_diff_refs(
    repo: Path,
    *,
    mode: str,
    base: str | None = None,
    branch: str | None = None,
    commit: str | None = None,
) -> tuple[str, str]:
    """Return (base_ref, head_ref) for the given review mode.

    base_ref is what reviewers diff against.
    head_ref is the commit/ref being reviewed.
    """
    if mode == "branch":
        if not base:
            raise ValueError("--base is required for branch mode")
        head = branch or "HEAD"
        # Validate both refs exist
        _run_git(repo, "rev-parse", "--verify", base)
        _run_git(repo, "rev-parse", "--verify", head)
        return base, head
    elif mode == "uncommitted":
        return "HEAD", "WORKING_TREE"
    elif mode == "commit":
        if not commit:
            raise ValueError("--commit is required for commit mode")
        _run_git(repo, "rev-parse", "--verify", commit)
        parent = f"{commit}~1"
        try:
            _run_git(repo, "rev-parse", "--verify", parent)
        except subprocess.CalledProcessError as exc:
            raise ValueError(
                f"Cannot resolve parent of {commit} — it may be the initial commit"
            ) from exc
        return parent, commit
    else:
        raise ValueError(f"Unknown review mode: {mode}")


def gather_diff_metadata(
    repo: Path,
    base_ref: str,
    head_ref: str,
) -> tuple[int, int, list[str]]:
    """Compute additions, deletions, and changed file paths from git diff."""
    if head_ref == "WORKING_TREE":
        numstat = _run_git(repo, "diff", "--numstat", base_ref)
        name_only = _run_git(repo, "diff", "--name-only", base_ref)
    else:
        numstat = _run_git(repo, "diff", "--numstat", f"{base_ref}...{head_ref}")
        name_only = _run_git(repo, "diff", "--name-only", f"{base_ref}...{head_ref}")

    additions = 0
    deletions = 0
    for line in numstat.splitlines():
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            try:
                additions += int(parts[0])
            except ValueError:
                pass
            try:
                deletions += int(parts[1])
            except ValueError:
                pass

    changed_files = [f for f in name_only.splitlines() if f.strip()]
    return additions, deletions, changed_files


def build_local_candidate(
    repo: Path,
    *,
    mode: str,
    base_ref: str,
    head_ref: str,
    head_sha: str,
    additions: int,
    deletions: int,
    changed_file_paths: list[str],
) -> PRCandidate:
    repo_name = repo.resolve().name

    if mode == "branch":
        title = f"Branch comparison: {head_ref} vs {base_ref}"
    elif mode == "uncommitted":
        title = "Uncommitted changes"
    elif mode == "commit":
        title = f"Commit {head_sha[:12]}"
    else:
        title = f"Local review ({mode})"

    return PRCandidate(
        owner="local",
        repo=repo_name,
        number=0,
        url=str(repo.resolve()),
        title=title,
        author_login="local",
        base_ref=base_ref,
        head_sha=head_sha,
        updated_at="",
        additions=additions,
        deletions=deletions,
        changed_file_paths=changed_file_paths,
        is_local=True,
    )
