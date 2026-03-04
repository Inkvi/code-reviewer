import subprocess

import pytest

from pr_reviewer.config import AppConfig
from pr_reviewer.preflight import run_preflight


def test_run_preflight_requires_claude_for_multi_reviewer_without_claude_enabled(
    monkeypatch,
) -> None:
    cfg = AppConfig(github_org="polymerdao", enabled_reviewers=["codex", "gemini"])

    def fake_which(cmd: str) -> str | None:
        if cmd == "claude":
            return None
        return f"/usr/bin/{cmd}"

    monkeypatch.setattr("pr_reviewer.preflight.shutil.which", fake_which)

    with pytest.raises(RuntimeError, match=r"Missing required commands: claude"):
        run_preflight(cfg)


def test_run_preflight_does_not_require_claude_for_single_gemini_reviewer(monkeypatch) -> None:
    cfg = AppConfig(github_org="polymerdao", enabled_reviewers=["gemini"])

    def fake_which(cmd: str) -> str | None:
        if cmd in {"gh", "gemini"}:
            return f"/usr/bin/{cmd}"
        return None

    commands: list[list[str]] = []

    def fake_run_command(args: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        stdout = "Inkvi\n" if args[:3] == ["gh", "api", "user"] else ""
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("pr_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("pr_reviewer.preflight.run_command", fake_run_command)

    result = run_preflight(cfg)

    assert result.viewer_login == "Inkvi"
    assert all(command[0] != "claude" for command in commands)
