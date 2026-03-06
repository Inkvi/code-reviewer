import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from code_reviewer.models import PRCandidate
from code_reviewer.shell import CommandError
from code_reviewer.workspace import PRWorkspace


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
    )


def test_prepare_creates_workspace_and_clones(tmp_path: Path) -> None:
    ws = PRWorkspace(tmp_path / "workspaces")
    pr = _sample_pr()
    commands: list[list[str]] = []

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        commands.append(args)
        if args[0] == "gh" and "clone" in args:
            # Simulate creating the workdir — args: gh repo clone owner/repo <workdir> ...
            workdir_path = args[4]
            Path(workdir_path).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("code_reviewer.workspace.run_command", side_effect=fake_run_command):
        workdir = ws.prepare(pr)

    assert workdir.exists()
    assert "polymerdao-obul-pr-64" in workdir.name
    assert len(commands) == 4  # clone, fetch base, fetch PR, checkout


def test_prepare_cleans_up_on_failure(tmp_path: Path) -> None:
    ws = PRWorkspace(tmp_path / "workspaces")
    pr = _sample_pr()
    call_count = 0

    def failing_run_command(args, **_kwargs):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Clone succeeds
            workdir_path = args[4]
            Path(workdir_path).mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        raise CommandError(args, 1, "", "fetch failed")

    with patch("code_reviewer.workspace.run_command", side_effect=failing_run_command):
        with pytest.raises(CommandError):
            ws.prepare(pr)

    # Workdir should be cleaned up after failure
    if (tmp_path / "workspaces").exists():
        assert len(list((tmp_path / "workspaces").iterdir())) == 0


def test_update_to_latest_fetches_and_checkouts(tmp_path: Path) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    pr = _sample_pr()
    commands: list[list[str]] = []

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        commands.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch("code_reviewer.workspace.run_command", side_effect=fake_run_command):
        PRWorkspace.update_to_latest(workdir, pr)

    assert len(commands) == 2
    assert "fetch" in commands[0]
    assert "checkout" in commands[1]


def test_cleanup_removes_workdir_when_keep_false(tmp_path: Path) -> None:
    ws = PRWorkspace(tmp_path, keep=False)
    workdir = tmp_path / "test-workspace"
    workdir.mkdir()
    (workdir / "file.txt").write_text("content")

    ws.cleanup(workdir)

    assert not workdir.exists()


def test_cleanup_preserves_workdir_when_keep_true(tmp_path: Path) -> None:
    ws = PRWorkspace(tmp_path, keep=True)
    workdir = tmp_path / "test-workspace"
    workdir.mkdir()
    (workdir / "file.txt").write_text("content")

    ws.cleanup(workdir)

    assert workdir.exists()
