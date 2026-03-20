"""Skill injection for review workspaces.

Copies skill folders (containing SKILL.md) into ``target/.agents/skills/``
so that agent backends (Claude, Codex, Gemini) discover them natively.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def inject_skills(skills_dir: Path, target_cwd: Path) -> None:
    """Copy local skill folders into target_cwd/.agents/skills/.

    For each subfolder in *skills_dir* containing a ``SKILL.md``:
    - If ``target_cwd/.agents/skills/<name>`` already exists: skip (repo version wins).
    - Otherwise: copy the folder.

    Does nothing if *skills_dir* doesn't exist or is empty.
    """
    if not skills_dir.is_dir():
        return

    agents_skills = target_cwd / ".agents" / "skills"
    has_skills = False

    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "SKILL.md").exists():
            logger.debug("Skipping %s — no SKILL.md", entry.name)
            continue

        if not has_skills:
            agents_skills.mkdir(parents=True, exist_ok=True)
            has_skills = True

        target = agents_skills / entry.name
        if target.exists():
            logger.info("Skill %s already exists in target, skipping", entry.name)
            continue

        shutil.copytree(entry, target, symlinks=False)
        logger.debug("Copied local skill %s -> %s", entry.name, target)


def _reject_external_symlinks(source: Path) -> None:
    """Raise if any symlink inside *source* points outside the source tree."""
    source_root = source.resolve()
    for item in source.rglob("*"):
        if item.is_symlink():
            link_target = item.resolve()
            if not link_target.is_relative_to(source_root):
                raise ValueError(f"Rejected symlink escaping source tree: {item} -> {link_target}")


def inject_skill_paths(skill_paths: list[Path], target_cwd: Path) -> None:
    """Copy remote skill directories into target_cwd/.agents/skills/.

    For each path in *skill_paths*:
    - Uses only the basename as the destination name (path traversal protection).
    - Validates the resolved destination stays under ``.agents/skills/``.
    - Rejects any symlinks pointing outside the source directory.
    - If ``target_cwd/.agents/skills/<name>`` already exists: skip.
    - Otherwise: copy the entire directory.

    Uses copy (not symlink) for concurrency safety and to prevent
    write-back attacks from review agents.
    """
    if not skill_paths:
        return

    agents_skills = target_cwd / ".agents" / "skills"
    agents_skills.mkdir(parents=True, exist_ok=True)

    for skill_path in skill_paths:
        name = Path(skill_path.name).name  # basename only
        target = agents_skills / name

        # Path traversal check
        resolved = target.resolve()
        if not resolved.is_relative_to(agents_skills.resolve()):
            logger.warning("Rejected path-traversal skill name: %s", name)
            continue

        if target.exists():
            logger.info("Skill %s already exists in target, skipping remote", name)
            continue

        # Reject symlinks that escape the source tree (supply-chain protection)
        _reject_external_symlinks(skill_path)

        shutil.copytree(skill_path, target, symlinks=True)
        logger.debug("Copied remote skill %s -> %s", name, target)


def remove_injected_skills(target_cwd: Path) -> None:
    """Remove .agents/skills/ from a workspace.

    Used before mid-review restarts to avoid untracked-file conflicts
    with ``git checkout``.  No-op if the directory doesn't exist.
    """
    agents_skills = target_cwd / ".agents" / "skills"
    if agents_skills.is_dir():
        shutil.rmtree(agents_skills)
        logger.debug("Removed injected skills at %s", agents_skills)
