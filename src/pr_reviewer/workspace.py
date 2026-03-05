from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from pr_reviewer.models import PRCandidate
from pr_reviewer.shell import run_command


class PRWorkspace:
    def __init__(self, root: Path, keep: bool = False) -> None:
        self.root = root
        self.keep = keep

    def prepare(self, pr: PRCandidate) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        name = f"{pr.owner}-{pr.repo}-pr-{pr.number}-{uuid4().hex[:8]}"
        workdir = self.root / name
        try:
            run_command(
                [
                    "gh",
                    "repo",
                    "clone",
                    f"{pr.owner}/{pr.repo}",
                    str(workdir),
                    "--",
                    "--quiet",
                    "--filter=blob:none",
                ]
            )
            run_command(["git", "-C", str(workdir), "fetch", "--quiet", "origin", pr.base_ref])
            run_command(
                [
                    "git",
                    "-C",
                    str(workdir),
                    "fetch",
                    "--quiet",
                    "origin",
                    f"pull/{pr.number}/head:pr-{pr.number}",
                ]
            )
            run_command(["git", "-C", str(workdir), "checkout", "--quiet", f"pr-{pr.number}"])
        except Exception:  # noqa: BLE001
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        return workdir

    @staticmethod
    def update_to_latest(workdir: Path, pr: PRCandidate) -> None:
        """Re-fetch and checkout the latest PR head in an existing workspace."""
        run_command(
            [
                "git",
                "-C",
                str(workdir),
                "fetch",
                "--quiet",
                "origin",
                f"pull/{pr.number}/head:pr-{pr.number}",
                "--force",
            ]
        )
        run_command(["git", "-C", str(workdir), "checkout", "--quiet", f"pr-{pr.number}"])

    def cleanup(self, workdir: Path) -> None:
        if self.keep:
            return
        shutil.rmtree(workdir, ignore_errors=True)
