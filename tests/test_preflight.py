import subprocess
import sys
import types

import pytest

from code_reviewer.config import AppConfig
from code_reviewer.preflight import run_preflight


def test_run_preflight_requires_claude_for_multi_reviewer_without_claude_enabled(
    monkeypatch,
) -> None:
    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex", "gemini"])

    def fake_which(cmd: str) -> str | None:
        if cmd == "claude":
            return None
        return f"/usr/bin/{cmd}"

    monkeypatch.setattr("code_reviewer.preflight.shutil.which", fake_which)

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

    monkeypatch.setattr("code_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("code_reviewer.preflight.run_command", fake_run_command)

    result = run_preflight(cfg)

    assert result.viewer_login == "Inkvi"
    assert ["gemini", "--version"] in commands
    assert ["gemini", "extensions", "list"] in commands
    assert all(command[0] != "claude" for command in commands)


def test_run_preflight_rejects_missing_gemini_code_review_extension(monkeypatch, tmp_path) -> None:
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

    monkeypatch.setattr("code_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("code_reviewer.preflight.run_command", fake_run_command)
    # Use tmp_path as home so filesystem fallback doesn't find the real extension
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))

    with pytest.raises(RuntimeError, match=r"requires the `code-review` extension"):
        run_preflight(cfg)


def test_run_preflight_requires_claude_when_triage_backend_is_claude(monkeypatch) -> None:
    cfg = AppConfig(
        github_orgs=["polymerdao"],
        enabled_reviewers=["codex"],
        triage_backend="claude",
    )

    def fake_which(cmd: str) -> str | None:
        if cmd == "claude":
            return None
        return f"/usr/bin/{cmd}"

    monkeypatch.setattr("code_reviewer.preflight.shutil.which", fake_which)

    with pytest.raises(RuntimeError, match=r"Missing required commands: claude"):
        run_preflight(cfg)


def test_run_preflight_requires_claude_when_lightweight_backend_is_claude(monkeypatch) -> None:
    cfg = AppConfig(
        github_orgs=["polymerdao"],
        enabled_reviewers=["codex"],
        lightweight_review_backend="claude",
    )

    def fake_which(cmd: str) -> str | None:
        if cmd == "claude":
            return None
        return f"/usr/bin/{cmd}"

    monkeypatch.setattr("code_reviewer.preflight.shutil.which", fake_which)

    with pytest.raises(RuntimeError, match=r"Missing required commands: claude"):
        run_preflight(cfg)


def test_run_preflight_skips_sdk_import_for_claude_cli_triage_backend(monkeypatch) -> None:
    cfg = AppConfig(
        github_orgs=["polymerdao"],
        enabled_reviewers=["codex"],
        triage_backend="claude",
        claude_backend="cli",
    )

    def fake_which(cmd: str) -> str | None:
        return f"/usr/bin/{cmd}"

    commands: list[list[str]] = []

    def fake_run_command(args: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[:3] == ["gh", "api", "user"]:
            stdout = "Inkvi\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("code_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("code_reviewer.preflight.run_command", fake_run_command)

    result = run_preflight(cfg)

    assert result.viewer_login == "Inkvi"
    assert ["claude", "-v"] in commands


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

    monkeypatch.setattr("code_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("code_reviewer.preflight.run_command", fake_run_command)

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

    monkeypatch.setattr("code_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("code_reviewer.preflight.run_command", fake_run_command)
    fake_module = types.ModuleType("claude_agent_sdk")
    fake_module.query = object()
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)

    result = run_preflight(cfg)

    assert result.viewer_login == "Inkvi"
    assert ["gemini", "--version"] in commands
    assert ["gemini", "extensions", "list"] not in commands


def test_run_preflight_gemini_reviewer_with_prompt_override_does_not_require_extension(
    monkeypatch,
) -> None:
    cfg = AppConfig(
        github_orgs=["polymerdao"],
        enabled_reviewers=["gemini"],
        full_review_prompt_path="/tmp/full_review.toml",
    )

    def fake_which(cmd: str) -> str | None:
        if cmd in {"gh", "gemini"}:
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

    monkeypatch.setattr("code_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("code_reviewer.preflight.run_command", fake_run_command)

    result = run_preflight(cfg)

    assert result.viewer_login == "Inkvi"
    assert ["gemini", "--version"] in commands
    assert ["gemini", "extensions", "list"] not in commands


def test_run_preflight_uses_app_slug_for_github_app_auth(monkeypatch) -> None:
    import io
    import json

    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["gemini"])

    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")

    def fake_which(cmd: str) -> str | None:
        if cmd in {"gh", "gemini"}:
            return f"/usr/bin/{cmd}"
        return None

    def fake_run_command(args: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        if args == ["gemini", "extensions", "list"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="✓ code-review (0.1.0)\n", stderr=""
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def fake_urlopen(req):
        body = json.dumps({"slug": "my-code-reviewer"}).encode()
        resp = io.BytesIO(body)
        resp.read = resp.read
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    monkeypatch.setattr("code_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("code_reviewer.preflight.run_command", fake_run_command)
    monkeypatch.setattr(
        "code_reviewer.github_app_auth._generate_jwt", lambda app_id, pk: "fake-jwt"
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = run_preflight(cfg)

    assert result.viewer_login == "my-code-reviewer[bot]"


def test_run_preflight_raises_when_app_slug_fails(monkeypatch) -> None:
    from urllib.error import HTTPError

    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["gemini"])

    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "67890")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "fake-key")

    def fake_which(cmd: str) -> str | None:
        if cmd in {"gh", "gemini"}:
            return f"/usr/bin/{cmd}"
        return None

    def fake_run_command(args: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def fake_urlopen(req):
        raise HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("code_reviewer.preflight.shutil.which", fake_which)
    monkeypatch.setattr("code_reviewer.preflight.run_command", fake_run_command)
    monkeypatch.setattr(
        "code_reviewer.github_app_auth._generate_jwt", lambda app_id, pk: "fake-jwt"
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Failed to resolve GitHub App slug"):
        run_preflight(cfg)
