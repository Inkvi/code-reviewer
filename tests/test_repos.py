from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from code_reviewer.repos import fetch_remote_skills, parse_github_tree_url


class TestParseGithubTreeUrl:
    def test_basic_url(self) -> None:
        owner, repo, ref, path = parse_github_tree_url(
            "https://github.com/org/repo/tree/main/skills/my-skill"
        )
        assert owner == "org"
        assert repo == "repo"
        assert ref == "main"
        assert path == "skills/my-skill"

    def test_nested_path(self) -> None:
        owner, repo, ref, path = parse_github_tree_url(
            "https://github.com/org/repo/tree/v2/deep/nested/skill"
        )
        assert owner == "org"
        assert repo == "repo"
        assert ref == "v2"
        assert path == "deep/nested/skill"

    def test_trailing_slash_stripped(self) -> None:
        _, _, _, path = parse_github_tree_url("https://github.com/org/repo/tree/main/skills/foo/")
        assert path == "skills/foo"

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub tree URL"):
            parse_github_tree_url("https://github.com/org/repo")

    def test_not_github_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub tree URL"):
            parse_github_tree_url("https://gitlab.com/org/repo/tree/main/foo")

    def test_no_path_after_ref_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub tree URL"):
            parse_github_tree_url("https://github.com/org/repo/tree/main")

    def test_ref_with_slashes(self) -> None:
        owner, repo, ref, path = parse_github_tree_url(
            "https://github.com/org/repo/tree/feature/foo/skills/bar"
        )
        assert owner == "org"
        assert repo == "repo"
        # The regex is greedy on ref — it captures the first path component.
        # Refs with slashes are ambiguous in GitHub tree URLs.
        assert ref == "feature"


class TestFetchRemoteSkills:
    def test_clones_and_returns_skill_path(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        url = "https://github.com/org/repo/tree/main/skills/my-skill"

        cache_path = base_dir / ".skill-repos" / "org" / "repo" / "main"

        async def fake_run(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "clone":
                # Simulate clone into the temp dir that gets renamed
                target = Path(cmd[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / ".git").mkdir()
                skill_dir = target / "skills" / "my-skill"
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\n")
            return (0, "", "")

        with patch("code_reviewer.repos.run_command_async", side_effect=fake_run):
            paths = asyncio.run(fetch_remote_skills([url], base_dir))

        assert len(paths) == 1
        assert paths[0] == cache_path / "skills" / "my-skill"
        assert (paths[0] / "SKILL.md").exists()

    def test_raises_on_clone_failure(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        url = "https://github.com/org/repo/tree/main/skills/my-skill"

        async def fake_run(cmd, **kwargs):
            return (1, "", "fatal: could not clone")

        with patch("code_reviewer.repos.run_command_async", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="Failed to clone"):
                asyncio.run(fetch_remote_skills([url], base_dir))

    def test_raises_on_missing_skill_md(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        url = "https://github.com/org/repo/tree/main/skills/my-skill"

        async def fake_run(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "clone":
                target = Path(cmd[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / ".git").mkdir()
                # Create skill dir but no SKILL.md
                (target / "skills" / "my-skill").mkdir(parents=True)
            return (0, "", "")

        with patch("code_reviewer.repos.run_command_async", side_effect=fake_run):
            with pytest.raises(FileNotFoundError, match="no SKILL.md"):
                asyncio.run(fetch_remote_skills([url], base_dir))

    def test_updates_existing_repo(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        url = "https://github.com/org/repo/tree/main/skills/my-skill"

        # Pre-create the cache as if already cloned
        cache_path = base_dir / ".skill-repos" / "org" / "repo" / "main"
        cache_path.mkdir(parents=True)
        (cache_path / ".git").mkdir()
        skill_dir = cache_path / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\n")

        commands_run: list[list[str]] = []

        async def fake_run(cmd, **kwargs):
            commands_run.append(list(cmd))
            return (0, "", "")

        with patch("code_reviewer.repos.run_command_async", side_effect=fake_run):
            paths = asyncio.run(fetch_remote_skills([url], base_dir))

        assert len(paths) == 1
        # Should have run fetch + reset, not clone
        assert any("fetch" in cmd for cmd in commands_run)
        assert not any("clone" in cmd for cmd in commands_run)

    def test_deduplicates_repos(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        url1 = "https://github.com/org/repo/tree/main/skills/skill-a"
        url2 = "https://github.com/org/repo/tree/main/skills/skill-b"

        clone_count = 0

        async def fake_run(cmd, **kwargs):
            nonlocal clone_count
            if cmd[0] == "git" and cmd[1] == "clone":
                clone_count += 1
                target = Path(cmd[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / ".git").mkdir()
                for skill_name in ("skill-a", "skill-b"):
                    sd = target / "skills" / skill_name
                    sd.mkdir(parents=True)
                    (sd / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n")
            return (0, "", "")

        with patch("code_reviewer.repos.run_command_async", side_effect=fake_run):
            paths = asyncio.run(fetch_remote_skills([url1, url2], base_dir))

        assert len(paths) == 2
        assert clone_count == 1  # Only one clone, not two

    def test_raises_on_fetch_failure(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        url = "https://github.com/org/repo/tree/main/skills/my-skill"

        # Pre-create the cache
        cache_path = base_dir / ".skill-repos" / "org" / "repo" / "main"
        cache_path.mkdir(parents=True)
        (cache_path / ".git").mkdir()

        async def fake_run(cmd, **kwargs):
            if "fetch" in cmd:
                return (1, "", "fatal: could not fetch")
            return (0, "", "")

        with patch("code_reviewer.repos.run_command_async", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="Failed to fetch"):
                asyncio.run(fetch_remote_skills([url], base_dir))

    def test_sanitizes_ref_with_slashes(self, tmp_path: Path) -> None:
        """Refs with slashes get sanitized for filesystem paths."""
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        url = "https://github.com/org/repo/tree/feature/skills/my-skill"

        # The regex captures "feature" as the ref (greedy first component).
        # Verify it uses sanitized path.
        cache_path = base_dir / ".skill-repos" / "org" / "repo" / "feature"

        async def fake_run(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "clone":
                target = Path(cmd[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / ".git").mkdir()
                skill_dir = target / "skills" / "my-skill"
                skill_dir.mkdir(parents=True)
                (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\n")
            return (0, "", "")

        with patch("code_reviewer.repos.run_command_async", side_effect=fake_run):
            paths = asyncio.run(fetch_remote_skills([url], base_dir))

        assert len(paths) == 1
        assert cache_path.exists()
