from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from code_reviewer.config import AppConfig
from code_reviewer.github_app_auth import is_github_app_auth
from code_reviewer.shell import CommandError, run_command


@dataclass(slots=True)
class PreflightResult:
    viewer_login: str


def run_preflight(config: AppConfig) -> PreflightResult:
    required = ["gh"]
    enabled = set(config.enabled_reviewers)
    uses_reconciler = len(enabled) >= 2
    reconciler_backends = set(config.reconciler_backend) if uses_reconciler else set()
    triage_backends = set(config.triage_backend)
    lightweight_backends = set(config.lightweight_review_backend)
    uses_claude_runtime = (
        "claude" in enabled
        or "claude" in reconciler_backends
        or "claude" in triage_backends
        or "claude" in lightweight_backends
    )
    uses_codex_cli = ("codex" in enabled and config.codex_backend == "cli") or (
        "codex" in reconciler_backends
    )
    uses_antigravity_cli = (
        "antigravity" in enabled
        or "antigravity" in reconciler_backends
        or "antigravity" in triage_backends
        or "antigravity" in lightweight_backends
    )
    uses_opencode_cli = (
        "opencode" in enabled
        or "opencode" in reconciler_backends
        or "opencode" in triage_backends
        or "opencode" in lightweight_backends
    )

    if "antigravity" in enabled and config.full_review_prompt_path is None:
        raise RuntimeError(
            "Antigravity reviewer requires full_review_prompt_path to be set "
            "(prompt mode is the only supported mode)."
        )

    if uses_claude_runtime:
        required.append("claude")
    if uses_codex_cli:
        required.append("codex")
    if uses_antigravity_cli:
        required.append("agy")
    if uses_opencode_cli:
        required.append("opencode")

    missing = [cmd for cmd in required if shutil.which(cmd) is None]
    if missing:
        raise RuntimeError(f"Missing required commands: {', '.join(missing)}")

    has_token = bool(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    if not has_token:
        try:
            run_command(["gh", "auth", "status"])
        except CommandError as exc:
            raise RuntimeError("gh auth is not configured. Run 'gh auth login'.") from exc

    if is_github_app_auth():
        # Installation tokens can't call /user — resolve the app slug
        # via a direct API call using JWT (Bearer auth, not gh CLI which uses token auth).
        import json
        import urllib.request

        from code_reviewer.github_app_auth import _generate_jwt

        app_id = os.environ["GITHUB_APP_ID"]
        private_key = os.environ["GITHUB_APP_PRIVATE_KEY"]
        jwt_token = _generate_jwt(app_id, private_key)
        try:
            req = urllib.request.Request(
                "https://api.github.com/app",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
            viewer_login = f"{data['slug']}[bot]"
        except Exception as exc:
            raise RuntimeError("Failed to resolve GitHub App slug via /app.") from exc
    else:
        try:
            login_proc = run_command(["gh", "api", "user", "--jq", ".login"])
            viewer_login = login_proc.stdout.strip()
        except CommandError as exc:
            raise RuntimeError("Failed to resolve GitHub user via gh api.") from exc

    if not viewer_login or viewer_login == "[bot]":
        raise RuntimeError("Could not determine authenticated GitHub login.")

    if uses_codex_cli:
        run_command(["codex", "--version"])

    if uses_claude_runtime:
        run_command(["claude", "-v"])

    uses_claude_sdk = uses_claude_runtime and config.claude_backend == "sdk"
    if uses_claude_sdk:
        try:
            from claude_agent_sdk import query  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            if "claude" in enabled:
                raise RuntimeError("Python package claude-agent-sdk is unavailable.") from exc
            raise RuntimeError(
                "Python package claude-agent-sdk is required for reconciler_backend=claude."
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

    if uses_opencode_cli:
        run_command(["opencode", "--version"])

    if uses_antigravity_cli:
        run_command(["agy", "--version"])

    return PreflightResult(viewer_login=viewer_login)
