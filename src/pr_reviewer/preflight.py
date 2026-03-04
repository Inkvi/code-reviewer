from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from pr_reviewer.config import AppConfig
from pr_reviewer.shell import CommandError, run_command


@dataclass(slots=True)
class PreflightResult:
    viewer_login: str


def run_preflight(config: AppConfig) -> PreflightResult:
    required = ["gh"]
    enabled = set(config.enabled_reviewers)
    uses_claude_reconciler = "claude" in enabled or len(enabled) >= 2
    if uses_claude_reconciler:
        required.append("claude")
    if "codex" in enabled and config.codex_backend == "cli":
        required.append("codex")
    if "gemini" in enabled:
        required.append("gemini")

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

    if "codex" in enabled and config.codex_backend == "cli":
        run_command(["codex", "--version"])

    if uses_claude_reconciler:
        run_command(["claude", "-v"])

    if uses_claude_reconciler:
        try:
            from claude_agent_sdk import query  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            if "claude" in enabled:
                raise RuntimeError("Python package claude-agent-sdk is unavailable.") from exc
            raise RuntimeError(
                "Python package claude-agent-sdk is required for multi-reviewer reconciliation."
            ) from exc

    if "codex" in enabled and config.codex_backend == "agents_sdk":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required for codex_backend=agents_sdk.")
        try:
            import agents  # noqa: F401
        except ModuleNotFoundError:
            try:
                import openai_agents  # noqa: F401
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "codex_backend=agents_sdk requires the OpenAI Agents SDK package."
                ) from exc

    if "gemini" in enabled:
        run_command(["gemini", "--version"])

    return PreflightResult(viewer_login=viewer_login)
