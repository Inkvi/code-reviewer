"""Remote skill repository fetching.

Clones GitHub repositories containing skill folders and resolves the
requested skill paths.  Uses file locking for concurrency safety.
"""

from __future__ import annotations

import fcntl
import logging
import re
import shutil
import tempfile
from pathlib import Path

from code_reviewer.shell import run_command_async

logger = logging.getLogger(__name__)

_GITHUB_TREE_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)$")


def parse_github_tree_url(url: str) -> tuple[str, str, str, str]:
    """Parse a GitHub tree URL into (owner, repo, ref, path).

    Example: ``https://github.com/org/repo/tree/main/skills/foo``
    Returns: ``("org", "repo", "main", "skills/foo")``
    """
    url = url.rstrip("/")
    m = _GITHUB_TREE_RE.match(url)
    if not m:
        raise ValueError(
            f"Invalid GitHub tree URL: {url!r}. "
            f"Expected: https://github.com/{{owner}}/{{repo}}/tree/{{ref}}/{{path}}"
        )
    return m.group(1), m.group(2), m.group(3), m.group(4)


def _sanitize_ref(ref: str) -> str:
    """Sanitize a git ref for use as a filesystem path component."""
    return ref.replace("/", "--")


def _skill_repos_dir(base_dir: Path) -> Path:
    return base_dir / ".skill-repos"


async def fetch_remote_skills(
    urls: list[str],
    base_dir: Path,
) -> list[Path]:
    """Fetch remote skills from GitHub tree URLs.

    Clones or updates repos, validates ``SKILL.md`` exists.
    Returns list of resolved skill directory paths.
    Raises on any failure (clone, fetch, or missing ``SKILL.md``).
    """
    root = _skill_repos_dir(base_dir)
    root.mkdir(parents=True, exist_ok=True)

    parsed: list[tuple[str, str, str, str]] = []
    for url in urls:
        parsed.append(parse_github_tree_url(url))

    # Clone/fetch unique repos
    fetched: set[tuple[str, str, str]] = set()
    for owner, repo, ref, _ in parsed:
        key = (owner, repo, ref)
        if key in fetched:
            continue
        fetched.add(key)

        sanitized_ref = _sanitize_ref(ref)
        local_path = root / owner / repo / sanitized_ref
        repo_url = f"https://github.com/{owner}/{repo}.git"
        lock_path = local_path.parent / f"{sanitized_ref}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                if (local_path / ".git").is_dir():
                    logger.info("Updating skill repo %s/%s@%s", owner, repo, ref)
                    code, _, stderr = await run_command_async(
                        ["git", "fetch", "origin", ref],
                        cwd=local_path,
                        timeout=120,
                    )
                    if code == 0:
                        await run_command_async(
                            ["git", "reset", "--hard", "FETCH_HEAD"],
                            cwd=local_path,
                            timeout=30,
                        )
                    else:
                        raise RuntimeError(
                            f"Failed to fetch skill repo {owner}/{repo}@{ref}: {stderr.strip()}"
                        )
                else:
                    logger.info("Cloning skill repo %s/%s@%s", owner, repo, ref)
                    # Atomic clone: clone to temp dir, then rename
                    tmp_clone = Path(
                        tempfile.mkdtemp(
                            prefix=f".tmp-{owner}-{repo}-",
                            dir=local_path.parent,
                        )
                    )
                    try:
                        code, _, stderr = await run_command_async(
                            [
                                "git",
                                "clone",
                                "--depth",
                                "1",
                                "--branch",
                                ref,
                                repo_url,
                                str(tmp_clone),
                            ],
                            timeout=300,
                        )
                        if code != 0:
                            raise RuntimeError(
                                f"Failed to clone skill repo {owner}/{repo}@{ref}: {stderr.strip()}"
                            )
                        tmp_clone.rename(local_path)
                    except Exception:
                        shutil.rmtree(tmp_clone, ignore_errors=True)
                        raise
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    # Resolve and validate skill paths
    result: list[Path] = []
    for owner, repo, ref, path in parsed:
        sanitized_ref = _sanitize_ref(ref)
        repo_root = (root / owner / repo / sanitized_ref).resolve()
        skill_dir = (root / owner / repo / sanitized_ref / path).resolve()
        # Path traversal check
        if not skill_dir.is_relative_to(repo_root):
            raise RuntimeError(f"Skill path escapes repo root: {path} in {owner}/{repo}@{ref}")
        if not (skill_dir / "SKILL.md").exists():
            raise FileNotFoundError(
                f"Skill not found: {path} in {owner}/{repo}@{ref} (no SKILL.md at {skill_dir})"
            )
        result.append(skill_dir)

    return result
