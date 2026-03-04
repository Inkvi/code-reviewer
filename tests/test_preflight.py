import subprocess
import sys
import types

import pytest

from pr_reviewer.config import AppConfig
from pr_reviewer.preflight import run_preflight


def test_run_preflight_requires_claude_for_multi_reviewer_without_claude_enabled(
    monkeypatch,
) -> None:
    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex", "gemini"])

    def fake_which(cmd: str) -> str | None:
        if cmd == "claude":
            return None
        return f"/usr/bin/{cmd}"

    monkeypatch.setattr("pr_reviewer.preflight.shutil.which", fake_which)

    with pytest.raises(RuntimeError, match=r"Missing required commands: claude"):
        run_preflight(cfg)


def test_run_preflight_does_not_require_claude_for_single_gemini_reviewer(monkeypatch) -> None:
    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["gemini"])

    def fake_which(cmd: str) -> str | None:
        if cmd in {"gh", "gemini"}:
            return f"/usr/bin/{cmd}"
        return None

    commands: list[list[str]] = []

    def fake_run_command(args: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[:3] == ["gh", "api", "user"]:
            stdout = "Inkvi\n"
        elif args == ["gemini", "extensions", "list"]:
            stdout = "✓ code-review (0.1.0)\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("pr_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("pr_reviewer.preflight.run_command", fake_run_command)

    result = run_preflight(cfg)

    assert result.viewer_login == "Inkvi"
    assert ["gemini", "extensions", "list"] in commands
    assert all(command[0] != "claude" for command in commands)


def test_run_preflight_rejects_missing_gemini_code_review_extension(monkeypatch) -> None:
    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["gemini"])

    def fake_which(cmd: str) -> str | None:
        if cmd in {"gh", "gemini"}:
            return f"/usr/bin/{cmd}"
        return None

    def fake_run_command(args: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["gh", "api", "user"]:
            stdout = "Inkvi\n"
        elif args == ["gemini", "extensions", "list"]:
            stdout = "✓ another-extension (0.1.0)\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("pr_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("pr_reviewer.preflight.run_command", fake_run_command)

    with pytest.raises(RuntimeError, match=r"requires the `code-review` extension"):
        run_preflight(cfg)


def test_run_preflight_does_not_require_claude_for_codex_reconciler(monkeypatch) -> None:
    cfg = AppConfig(
        github_orgs=["polymerdao"],
        enabled_reviewers=["codex", "gemini"],
        reconciler_backend="codex",
    )

    def fake_which(cmd: str) -> str | None:
        if cmd in {"gh", "codex", "gemini"}:
            return f"/usr/bin/{cmd}"
        return None

    commands: list[list[str]] = []

    def fake_run_command(args: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[:3] == ["gh", "api", "user"]:
            stdout = "Inkvi\n"
        elif args == ["gemini", "extensions", "list"]:
            stdout = "✓ code-review (0.1.0)\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("pr_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("pr_reviewer.preflight.run_command", fake_run_command)

    result = run_preflight(cfg)

    assert result.viewer_login == "Inkvi"
    assert all(command[0] != "claude" for command in commands)


def test_run_preflight_gemini_reconciler_does_not_require_extension_when_not_reviewer(
    monkeypatch,
) -> None:
    cfg = AppConfig(
        github_orgs=["polymerdao"],
        enabled_reviewers=["codex", "claude"],
        reconciler_backend="gemini",
    )

    def fake_which(cmd: str) -> str | None:
        if cmd in {"gh", "codex", "claude", "gemini"}:
            return f"/usr/bin/{cmd}"
        return None

    commands: list[list[str]] = []

    def fake_run_command(args: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[:3] == ["gh", "api", "user"]:
            stdout = "Inkvi\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("pr_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("pr_reviewer.preflight.run_command", fake_run_command)
    fake_module = types.ModuleType("claude_agent_sdk")
    fake_module.query = object()
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)

    result = run_preflight(cfg)

    assert result.viewer_login == "Inkvi"
    assert ["gemini", "extensions", "list"] not in commands
