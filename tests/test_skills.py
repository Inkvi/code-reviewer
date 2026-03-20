from __future__ import annotations

from pathlib import Path

from code_reviewer.skills import inject_skill_paths, inject_skills, remove_injected_skills


def _make_skill(skills_dir: Path, name: str) -> Path:
    """Create a minimal valid skill folder."""
    skill_path = skills_dir / name
    skill_path.mkdir(parents=True)
    (skill_path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill\n---\nInstructions here.\n",
        encoding="utf-8",
    )
    return skill_path


class TestInjectSkills:
    def test_copies_skills(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        _make_skill(skills_dir, "code-review")

        target = tmp_path / "worktree"
        target.mkdir()

        inject_skills(skills_dir, target)

        dest = target / ".agents" / "skills" / "code-review"
        assert dest.is_dir()
        assert not dest.is_symlink()
        assert (dest / "SKILL.md").exists()

    def test_creates_agents_skills_dir(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        _make_skill(skills_dir, "lint")

        target = tmp_path / "worktree"
        target.mkdir()

        inject_skills(skills_dir, target)
        assert (target / ".agents" / "skills").is_dir()

    def test_skips_existing_skill(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        _make_skill(skills_dir, "code-review")

        target = tmp_path / "worktree"
        target.mkdir()
        existing = target / ".agents" / "skills" / "code-review"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text("existing", encoding="utf-8")

        inject_skills(skills_dir, target)

        assert (existing / "SKILL.md").read_text() == "existing"

    def test_skips_folders_without_skill_md(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "not-a-skill").mkdir()

        target = tmp_path / "worktree"
        target.mkdir()

        inject_skills(skills_dir, target)
        assert not (target / ".agents" / "skills" / "not-a-skill").exists()

    def test_multiple_skills(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        _make_skill(skills_dir, "code-review")
        _make_skill(skills_dir, "lint")

        target = tmp_path / "worktree"
        target.mkdir()

        inject_skills(skills_dir, target)

        assert (target / ".agents" / "skills" / "code-review").is_dir()
        assert (target / ".agents" / "skills" / "lint").is_dir()

    def test_empty_skills_dir(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        target = tmp_path / "worktree"
        target.mkdir()

        inject_skills(skills_dir, target)
        assert not (target / ".agents").exists()

    def test_nonexistent_skills_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "worktree"
        target.mkdir()

        inject_skills(tmp_path / "no-skills", target)
        assert not (target / ".agents").exists()


class TestInjectSkillPaths:
    def test_copies_skill_into_target(self, tmp_path: Path) -> None:
        remote = tmp_path / "remote"
        remote.mkdir()
        skill = _make_skill(remote, "code-review")
        (skill / "extra.txt").write_text("extra content", encoding="utf-8")

        target = tmp_path / "worktree"
        target.mkdir()

        inject_skill_paths([skill], target)

        dest = target / ".agents" / "skills" / "code-review"
        assert dest.is_dir()
        assert not dest.is_symlink()
        assert (dest / "SKILL.md").exists()
        assert (dest / "extra.txt").read_text() == "extra content"

    def test_skips_existing_skill(self, tmp_path: Path) -> None:
        remote = tmp_path / "remote"
        remote.mkdir()
        skill = _make_skill(remote, "code-review")

        target = tmp_path / "worktree"
        target.mkdir()
        existing = target / ".agents" / "skills" / "code-review"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text("existing", encoding="utf-8")

        inject_skill_paths([skill], target)

        assert (existing / "SKILL.md").read_text() == "existing"

    def test_multiple_skills(self, tmp_path: Path) -> None:
        remote = tmp_path / "remote"
        remote.mkdir()
        skill_a = _make_skill(remote, "skill-a")
        skill_b = _make_skill(remote, "skill-b")

        target = tmp_path / "worktree"
        target.mkdir()

        inject_skill_paths([skill_a, skill_b], target)

        assert (target / ".agents" / "skills" / "skill-a" / "SKILL.md").exists()
        assert (target / ".agents" / "skills" / "skill-b" / "SKILL.md").exists()

    def test_first_wins_on_duplicate_name(self, tmp_path: Path) -> None:
        remote1 = tmp_path / "remote1"
        remote1.mkdir()
        skill1 = _make_skill(remote1, "dupe")
        (skill1 / "marker.txt").write_text("first", encoding="utf-8")

        remote2 = tmp_path / "remote2"
        remote2.mkdir()
        skill2 = _make_skill(remote2, "dupe")
        (skill2 / "marker.txt").write_text("second", encoding="utf-8")

        target = tmp_path / "worktree"
        target.mkdir()

        inject_skill_paths([skill1, skill2], target)

        marker = target / ".agents" / "skills" / "dupe" / "marker.txt"
        assert marker.read_text() == "first"

    def test_rejects_external_symlink(self, tmp_path: Path) -> None:
        """Symlinks pointing outside the skill source tree are rejected."""
        import os

        remote = tmp_path / "remote"
        remote.mkdir()
        skill = _make_skill(remote, "evil-skill")

        # Create a symlink inside the skill that points outside
        secret = tmp_path / "secret.txt"
        secret.write_text("sensitive data", encoding="utf-8")
        os.symlink(str(secret), str(skill / "stolen.txt"))

        target = tmp_path / "worktree"
        target.mkdir()

        import pytest

        with pytest.raises(ValueError, match="Rejected symlink"):
            inject_skill_paths([skill], target)

        # Skill should not have been copied
        assert not (target / ".agents" / "skills" / "evil-skill").exists()

    def test_allows_internal_symlink(self, tmp_path: Path) -> None:
        """Symlinks within the skill directory are fine."""
        import os

        remote = tmp_path / "remote"
        remote.mkdir()
        skill = _make_skill(remote, "good-skill")
        (skill / "real.txt").write_text("content", encoding="utf-8")
        os.symlink("real.txt", str(skill / "link.txt"))

        target = tmp_path / "worktree"
        target.mkdir()

        inject_skill_paths([skill], target)
        assert (target / ".agents" / "skills" / "good-skill" / "link.txt").exists()

    def test_empty_list_noop(self, tmp_path: Path) -> None:
        target = tmp_path / "worktree"
        target.mkdir()
        inject_skill_paths([], target)
        assert not (target / ".agents").exists()


class TestRemoveInjectedSkills:
    def test_removes_skills_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "worktree"
        skills_dir = target / ".agents" / "skills" / "test-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("test", encoding="utf-8")

        remove_injected_skills(target)

        assert not (target / ".agents" / "skills").exists()

    def test_noop_when_absent(self, tmp_path: Path) -> None:
        target = tmp_path / "worktree"
        target.mkdir()

        remove_injected_skills(target)
        # Should not raise
