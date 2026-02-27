from __future__ import annotations

import shutil
from dataclasses import dataclass

from pr_reviewer.shell import CommandError, run_command


@dataclass(slots=True)
class PreflightResult:
    viewer_login: str


def run_preflight() -> PreflightResult:
    required = ["gh", "codex", "claude"]
    missing = [cmd for cmd in required if shutil.which(cmd) is None]
    if missing:
        raise RuntimeError(f"Missing required commands: {', '.join(missing)}")

    try:
        run_command(["gh", "auth", "status"])
    except CommandError as exc:
        raise RuntimeError("gh auth is not configured. Run 'gh auth login'.") from exc

    try:
        login_proc = run_command(["gh", "api", "user", "--jq", ".login"])
    except CommandError as exc:
        raise RuntimeError("Failed to resolve GitHub user via gh api.") from exc

    viewer_login = login_proc.stdout.strip()
    if not viewer_login:
        raise RuntimeError("Could not determine authenticated GitHub login.")

    run_command(["codex", "--version"])
    run_command(["claude", "-v"])

    try:
        from claude_agent_sdk import query  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Python package claude-agent-sdk is unavailable.") from exc

    return PreflightResult(viewer_login=viewer_login)
