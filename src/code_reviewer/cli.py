from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.table import Table

from code_reviewer.config import AppConfig, default_config, load_config
from code_reviewer.daemon import run_cycle, start_daemon
from code_reviewer.github import GitHubClient
from code_reviewer.github_app_auth import refresh_github_token
from code_reviewer.local_review import (
    build_local_candidate,
    gather_diff_metadata,
    resolve_diff_refs,
    resolve_head_sha,
    validate_git_repo,
)
from code_reviewer.logger import console, error, info, redirect_to_stderr, warn
from code_reviewer.models import ProcessingResult
from code_reviewer.preflight import run_preflight
from code_reviewer.processor import process_candidate, process_local_review
from code_reviewer.prompts import get_default_prompt_spec_path
from code_reviewer.state import StateStore
from code_reviewer.webhook import WebhookConfig, run_server
from code_reviewer.workspace import PRWorkspace

app = typer.Typer(add_completion=False, help="AI code review tool")
ConfigOption = Annotated[
    Path,
    typer.Option(
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        file_okay=True,
        readable=True,
        help="Path to TOML config file.",
    ),
]
OptionalConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        dir_okay=False,
        file_okay=True,
        readable=True,
        help="Path to TOML config file. Uses defaults when not provided.",
    ),
]
EnabledReviewerOption = Annotated[
    list[str] | None,
    typer.Option(
        "--enabled-reviewer",
        "-r",
        help=("Override enabled_reviewers from config. Repeat flag to enable multiple reviewers."),
    ),
]
CodexBackendOption = Annotated[
    str | None,
    typer.Option(
        "--codex-backend",
        help=("Override codex_backend from config. Allowed: cli, agents_sdk."),
    ),
]
ClaudeBackendOption = Annotated[
    str | None,
    typer.Option(
        "--claude-backend",
        help=("Override claude_backend from config. Allowed: sdk, cli."),
    ),
]
ClaudeModelOption = Annotated[
    str | None,
    typer.Option(
        "--claude-model",
        help="Override claude_model from config.",
    ),
]
ClaudeReasoningEffortOption = Annotated[
    str | None,
    typer.Option(
        "--claude-reasoning-effort",
        help="Override claude_reasoning_effort from config. Allowed: low, medium, high, max.",
    ),
]
ReconcilerModelOption = Annotated[
    str | None,
    typer.Option(
        "--reconciler-model",
        help="Override reconciler_model from config.",
    ),
]
ReconcilerReasoningEffortOption = Annotated[
    str | None,
    typer.Option(
        "--reconciler-reasoning-effort",
        help=("Override reconciler_reasoning_effort from config. Allowed: low, medium, high, max."),
    ),
]
ReconcilerBackendOption = Annotated[
    str | None,
    typer.Option(
        "--reconciler-backend",
        help="Override reconciler_backend from config. Allowed: claude, codex, gemini.",
    ),
]
CodexModelOption = Annotated[
    str | None,
    typer.Option(
        "--codex-model",
        help="Override codex_model from config.",
    ),
]
CodexReasoningEffortOption = Annotated[
    str | None,
    typer.Option(
        "--codex-reasoning-effort",
        help="Override codex_reasoning_effort from config. Allowed: low, medium, high.",
    ),
]
GeminiModelOption = Annotated[
    str | None,
    typer.Option(
        "--gemini-model",
        help="Override gemini_model from config.",
    ),
]
GeminiFallbackModelOption = Annotated[
    str | None,
    typer.Option(
        "--gemini-fallback-model",
        help="Override gemini_fallback_model from config.",
    ),
]
AutoPostReviewOption = Annotated[
    bool | None,
    typer.Option(
        "--auto-post-review/--no-auto-post-review",
        help="Override auto_post_review from config.",
    ),
]
SlashCommandEnabledOption = Annotated[
    bool | None,
    typer.Option(
        "--slash-command-enabled/--no-slash-command-enabled",
        help="Override slash_command_enabled from config.",
    ),
]
TriageBackendOption = Annotated[
    str | None,
    typer.Option(
        "--triage-backend",
        help="Override triage_backend from config. Allowed: claude, codex, gemini.",
    ),
]
TriageModelOption = Annotated[
    str | None,
    typer.Option(
        "--triage-model",
        help="Override triage_model from config.",
    ),
]
LightweightReviewBackendOption = Annotated[
    str | None,
    typer.Option(
        "--lightweight-review-backend",
        help="Override lightweight_review_backend from config. Allowed: claude, codex, gemini.",
    ),
]
LightweightReviewModelOption = Annotated[
    str | None,
    typer.Option(
        "--lightweight-review-model",
        help="Override lightweight_review_model from config.",
    ),
]
LightweightReviewReasoningEffortOption = Annotated[
    str | None,
    typer.Option(
        "--lightweight-review-reasoning-effort",
        help=(
            "Override lightweight_review_reasoning_effort from config. "
            "Allowed: low, medium, high, max."
        ),
    ),
]
ReviewOwnOption = Annotated[
    bool,
    typer.Option(
        "--review-own",
        help="Review your own PRs (overrides skip_own_prs config).",
    ),
]
UseSavedReviewOption = Annotated[
    bool,
    typer.Option(
        "--use-saved-review",
        help=(
            "If a saved review markdown already exists, reuse it and continue with posting/"
            "submission instead of generating a new review."
        ),
    ),
]
PrUrlOption = Annotated[
    list[str] | None,
    typer.Option(
        "--pr-url",
        help="GitHub pull request URL to review directly. Repeat for multiple PRs.",
    ),
]
OutputFormatOption = Annotated[
    str,
    typer.Option(
        "--output-format",
        help="Output format: text (default) or json.",
    ),
]


def _load_config_or_default(config_path: Path | None) -> AppConfig:
    """Load config from file, or return defaults if no path given.

    When config_path is None, tries ./config.toml and falls back to defaults.
    When config_path is explicitly provided, raises BadParameter if missing.
    """
    if config_path is None:
        fallback = Path("config.toml")
        if fallback.exists():
            return load_config(fallback)
        return default_config()
    try:
        return load_config(config_path)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _require_github_orgs(config: AppConfig) -> None:
    """Raise if github_orgs is empty (required for polling commands)."""
    if not config.github_orgs:
        raise typer.BadParameter(
            "github_orgs must be set with at least one owner. "
            "Provide a config file with --config or set github_orgs in config.toml."
        )


def _apply_enabled_reviewer_override(
    config: AppConfig, enabled_reviewer: list[str] | None
) -> AppConfig:
    if not enabled_reviewer:
        return config
    payload = config.model_dump()
    payload["enabled_reviewers"] = enabled_reviewer
    try:
        return AppConfig.model_validate(payload)
    except ValidationError as exc:
        raise typer.BadParameter(
            f"Invalid --enabled-reviewer value(s): {exc.errors(include_url=False)}"
        ) from exc


def _apply_codex_backend_override(config: AppConfig, codex_backend: str | None) -> AppConfig:
    return _apply_field_override(config, "codex_backend", codex_backend, "--codex-backend")


def _apply_claude_backend_override(config: AppConfig, claude_backend: str | None) -> AppConfig:
    return _apply_field_override(config, "claude_backend", claude_backend, "--claude-backend")


def _apply_field_override(
    config: AppConfig,
    field_name: str,
    value: str | None,
    flag_name: str,
) -> AppConfig:
    if value is None:
        return config
    payload = config.model_dump()
    payload[field_name] = value
    try:
        return AppConfig.model_validate(payload)
    except ValidationError as exc:
        raise typer.BadParameter(
            f"Invalid {flag_name} value: {exc.errors(include_url=False)}"
        ) from exc


def _apply_bool_override(
    config: AppConfig,
    field_name: str,
    value: bool | None,
    flag_name: str,
) -> AppConfig:
    if value is None:
        return config
    payload = config.model_dump()
    payload[field_name] = value
    try:
        return AppConfig.model_validate(payload)
    except ValidationError as exc:
        raise typer.BadParameter(
            f"Invalid {flag_name} value: {exc.errors(include_url=False)}"
        ) from exc


def _resolve_reconciler_settings(config: AppConfig) -> tuple[list[str], str | None, str | None]:
    backends = config.reconciler_backend
    primary = backends[0]
    if primary == "claude":
        model = config.reconciler_model or config.claude_model
        reasoning_effort = config.reconciler_reasoning_effort or config.claude_reasoning_effort
    elif primary == "codex":
        model = config.reconciler_model or config.codex_model
        reasoning_effort = config.reconciler_reasoning_effort or config.codex_reasoning_effort
    else:
        model = config.reconciler_model or config.gemini_model
        reasoning_effort = None
    return backends, model, reasoning_effort


def _prompt_override_display(path_value: str | None, *, step: str) -> str:
    if path_value is None:
        return f"default ({get_default_prompt_spec_path(step)})"
    return str(Path(path_value).resolve())


def _load_config_with_overrides(
    config_path: Path | None,
    enabled_reviewer: list[str] | None,
    codex_backend: str | None,
    claude_backend: str | None,
    claude_model: str | None,
    claude_reasoning_effort: str | None,
    reconciler_backend: str | None,
    reconciler_model: str | None,
    reconciler_reasoning_effort: str | None,
    codex_model: str | None,
    codex_reasoning_effort: str | None,
    auto_post_review: bool | None,
    gemini_model: str | None,
    gemini_fallback_model: str | None,
    slash_command_enabled: bool | None,
    triage_backend: str | None,
    triage_model: str | None,
    lightweight_review_backend: str | None,
    lightweight_review_model: str | None,
    lightweight_review_reasoning_effort: str | None,
) -> AppConfig:
    config = _load_config_or_default(config_path)
    config = _apply_enabled_reviewer_override(config, enabled_reviewer)
    config = _apply_codex_backend_override(config, codex_backend)
    config = _apply_claude_backend_override(config, claude_backend)
    config = _apply_field_override(config, "claude_model", claude_model, "--claude-model")
    config = _apply_field_override(
        config,
        "claude_reasoning_effort",
        claude_reasoning_effort,
        "--claude-reasoning-effort",
    )
    config = _apply_field_override(
        config,
        "reconciler_backend",
        reconciler_backend,
        "--reconciler-backend",
    )
    config = _apply_field_override(
        config,
        "reconciler_model",
        reconciler_model,
        "--reconciler-model",
    )
    config = _apply_field_override(
        config,
        "reconciler_reasoning_effort",
        reconciler_reasoning_effort,
        "--reconciler-reasoning-effort",
    )
    config = _apply_field_override(config, "codex_model", codex_model, "--codex-model")
    config = _apply_field_override(
        config,
        "codex_reasoning_effort",
        codex_reasoning_effort,
        "--codex-reasoning-effort",
    )
    config = _apply_bool_override(
        config,
        "auto_post_review",
        auto_post_review,
        "--auto-post-review/--no-auto-post-review",
    )
    config = _apply_field_override(config, "gemini_model", gemini_model, "--gemini-model")
    config = _apply_field_override(
        config, "gemini_fallback_model", gemini_fallback_model, "--gemini-fallback-model"
    )
    config = _apply_bool_override(
        config,
        "slash_command_enabled",
        slash_command_enabled,
        "--slash-command-enabled/--no-slash-command-enabled",
    )
    config = _apply_field_override(config, "triage_backend", triage_backend, "--triage-backend")
    config = _apply_field_override(config, "triage_model", triage_model, "--triage-model")
    config = _apply_field_override(
        config,
        "lightweight_review_backend",
        lightweight_review_backend,
        "--lightweight-review-backend",
    )
    config = _apply_field_override(
        config, "lightweight_review_model", lightweight_review_model, "--lightweight-review-model"
    )
    config = _apply_field_override(
        config,
        "lightweight_review_reasoning_effort",
        lightweight_review_reasoning_effort,
        "--lightweight-review-reasoning-effort",
    )
    return config


def _load_runtime(
    config_path: Path | None,
    enabled_reviewer: list[str] | None,
    codex_backend: str | None,
    claude_backend: str | None,
    claude_model: str | None,
    claude_reasoning_effort: str | None,
    reconciler_backend: str | None,
    reconciler_model: str | None,
    reconciler_reasoning_effort: str | None,
    codex_model: str | None,
    codex_reasoning_effort: str | None,
    auto_post_review: bool | None,
    gemini_model: str | None,
    gemini_fallback_model: str | None,
    slash_command_enabled: bool | None,
    triage_backend: str | None,
    triage_model: str | None,
    lightweight_review_backend: str | None,
    lightweight_review_model: str | None,
    lightweight_review_reasoning_effort: str | None,
) -> tuple[AppConfig, StateStore]:
    config = _load_config_with_overrides(
        config_path,
        enabled_reviewer,
        codex_backend,
        claude_backend,
        claude_model,
        claude_reasoning_effort,
        reconciler_backend,
        reconciler_model,
        reconciler_reasoning_effort,
        codex_model,
        codex_reasoning_effort,
        auto_post_review,
        gemini_model,
        gemini_fallback_model,
        slash_command_enabled,
        triage_backend,
        triage_model,
        lightweight_review_backend,
        lightweight_review_model,
        lightweight_review_reasoning_effort,
    )
    store = StateStore(Path(config.state_file))
    store.acquire_lock()
    store.load()
    return config, store


def _target_pr_urls_for_run_once(
    pr_url: list[str] | None,
    *,
    use_saved_review: bool,
) -> list[str]:
    deduped_urls = list(dict.fromkeys(pr_url or []))
    if use_saved_review and not deduped_urls:
        raise typer.BadParameter("--use-saved-review requires at least one --pr-url value.")
    return deduped_urls


@app.command("check")
def check_command(
    config: ConfigOption = Path("config.toml"),
    enabled_reviewer: EnabledReviewerOption = None,
    codex_backend: CodexBackendOption = None,
    claude_backend: ClaudeBackendOption = None,
    claude_model: ClaudeModelOption = None,
    claude_reasoning_effort: ClaudeReasoningEffortOption = None,
    reconciler_backend: ReconcilerBackendOption = None,
    reconciler_model: ReconcilerModelOption = None,
    reconciler_reasoning_effort: ReconcilerReasoningEffortOption = None,
    codex_model: CodexModelOption = None,
    codex_reasoning_effort: CodexReasoningEffortOption = None,
    auto_post_review: AutoPostReviewOption = None,
    gemini_model: GeminiModelOption = None,
    gemini_fallback_model: GeminiFallbackModelOption = None,
    slash_command_enabled: SlashCommandEnabledOption = None,
    triage_backend: TriageBackendOption = None,
    triage_model: TriageModelOption = None,
    lightweight_review_backend: LightweightReviewBackendOption = None,
    lightweight_review_model: LightweightReviewModelOption = None,
    lightweight_review_reasoning_effort: LightweightReviewReasoningEffortOption = None,
) -> None:
    """Run preflight checks and print runtime summary."""
    cfg = load_config(config)
    _require_github_orgs(cfg)
    cfg = _apply_enabled_reviewer_override(cfg, enabled_reviewer)
    cfg = _apply_codex_backend_override(cfg, codex_backend)
    cfg = _apply_claude_backend_override(cfg, claude_backend)
    cfg = _apply_field_override(cfg, "claude_model", claude_model, "--claude-model")
    cfg = _apply_field_override(
        cfg,
        "claude_reasoning_effort",
        claude_reasoning_effort,
        "--claude-reasoning-effort",
    )
    cfg = _apply_field_override(
        cfg,
        "reconciler_backend",
        reconciler_backend,
        "--reconciler-backend",
    )
    cfg = _apply_field_override(cfg, "reconciler_model", reconciler_model, "--reconciler-model")
    cfg = _apply_field_override(
        cfg,
        "reconciler_reasoning_effort",
        reconciler_reasoning_effort,
        "--reconciler-reasoning-effort",
    )
    cfg = _apply_field_override(cfg, "codex_model", codex_model, "--codex-model")
    cfg = _apply_field_override(
        cfg,
        "codex_reasoning_effort",
        codex_reasoning_effort,
        "--codex-reasoning-effort",
    )
    cfg = _apply_bool_override(
        cfg,
        "auto_post_review",
        auto_post_review,
        "--auto-post-review/--no-auto-post-review",
    )
    cfg = _apply_field_override(cfg, "gemini_model", gemini_model, "--gemini-model")
    cfg = _apply_field_override(
        cfg, "gemini_fallback_model", gemini_fallback_model, "--gemini-fallback-model"
    )
    cfg = _apply_bool_override(
        cfg,
        "slash_command_enabled",
        slash_command_enabled,
        "--slash-command-enabled/--no-slash-command-enabled",
    )
    cfg = _apply_field_override(cfg, "triage_backend", triage_backend, "--triage-backend")
    cfg = _apply_field_override(cfg, "triage_model", triage_model, "--triage-model")
    cfg = _apply_field_override(
        cfg,
        "lightweight_review_backend",
        lightweight_review_backend,
        "--lightweight-review-backend",
    )
    cfg = _apply_field_override(
        cfg, "lightweight_review_model", lightweight_review_model, "--lightweight-review-model"
    )
    cfg = _apply_field_override(
        cfg,
        "lightweight_review_reasoning_effort",
        lightweight_review_reasoning_effort,
        "--lightweight-review-reasoning-effort",
    )
    refresh_github_token()
    preflight = run_preflight(cfg)

    table = Table(title="code-reviewer check")
    table.add_column("Item")
    table.add_column("Value")
    table.add_row("GitHub owners", ", ".join(cfg.github_owners))
    table.add_row("Viewer", preflight.viewer_login)
    table.add_row("Poll interval", str(cfg.poll_interval_seconds))
    table.add_row("Auto post", str(cfg.auto_post_review))
    table.add_row("Auto submit decision", str(cfg.auto_submit_review_decision))
    table.add_row("Include reviewer stderr", str(cfg.include_reviewer_stderr))
    table.add_row("Enabled reviewers", ", ".join(cfg.enabled_reviewers))
    table.add_row("Claude backend", cfg.claude_backend)
    table.add_row("Claude model", cfg.claude_model or "default")
    table.add_row("Claude reasoning effort", cfg.claude_reasoning_effort or "default")
    reconciler_backend_value, reconciler_model_value, reconciler_effort_value = (
        _resolve_reconciler_settings(cfg)
    )
    reconciler_primary = reconciler_backend_value[0]
    reconciler_effort_display = (
        reconciler_effort_value or "default" if reconciler_primary != "gemini" else "n/a"
    )
    table.add_row("Reconciler backend", " > ".join(reconciler_backend_value))
    table.add_row("Reconciler model", reconciler_model_value or "default")
    table.add_row("Reconciler reasoning effort", reconciler_effort_display)
    table.add_row("Codex backend", cfg.codex_backend)
    table.add_row("Codex model", cfg.codex_model)
    table.add_row("Codex reasoning effort", cfg.codex_reasoning_effort or "default")
    table.add_row("Gemini model", cfg.gemini_model or "default")
    table.add_row("Gemini fallback model", cfg.gemini_fallback_model or "none")
    table.add_row("Slash command enabled", str(cfg.slash_command_enabled))
    table.add_row("Triage backend", " > ".join(cfg.triage_backend))
    table.add_row("Triage model", cfg.triage_model or "default")
    table.add_row("Triage prompt", _prompt_override_display(cfg.triage_prompt_path, step="triage"))
    table.add_row("Triage timeout", str(cfg.triage_timeout_seconds))
    table.add_row("Lightweight review backend", " > ".join(cfg.lightweight_review_backend))
    table.add_row("Lightweight review model", cfg.lightweight_review_model or "default")
    table.add_row(
        "Lightweight review prompt",
        _prompt_override_display(cfg.lightweight_review_prompt_path, step="lightweight_review"),
    )
    lw_effort = cfg.lightweight_review_reasoning_effort or "default"
    table.add_row("Lightweight review reasoning effort", lw_effort)
    table.add_row("Lightweight review timeout", str(cfg.lightweight_review_timeout_seconds))
    table.add_row(
        "Full review prompt",
        _prompt_override_display(cfg.full_review_prompt_path, step="full_review"),
    )
    table.add_row(
        "Reconcile prompt",
        _prompt_override_display(cfg.reconcile_prompt_path, step="reconcile"),
    )
    table.add_row("Trigger mode", cfg.trigger_mode)
    table.add_row("Output dir", str(Path(cfg.output_dir).resolve()))
    table.add_row("State file", str(Path(cfg.state_file).resolve()))
    console.print(table)


@app.command("run-once")
def run_once_command(
    config: OptionalConfigOption = None,
    enabled_reviewer: EnabledReviewerOption = None,
    codex_backend: CodexBackendOption = None,
    claude_backend: ClaudeBackendOption = None,
    claude_model: ClaudeModelOption = None,
    claude_reasoning_effort: ClaudeReasoningEffortOption = None,
    reconciler_backend: ReconcilerBackendOption = None,
    reconciler_model: ReconcilerModelOption = None,
    reconciler_reasoning_effort: ReconcilerReasoningEffortOption = None,
    codex_model: CodexModelOption = None,
    codex_reasoning_effort: CodexReasoningEffortOption = None,
    auto_post_review: AutoPostReviewOption = None,
    gemini_model: GeminiModelOption = None,
    gemini_fallback_model: GeminiFallbackModelOption = None,
    slash_command_enabled: SlashCommandEnabledOption = None,
    triage_backend: TriageBackendOption = None,
    triage_model: TriageModelOption = None,
    lightweight_review_backend: LightweightReviewBackendOption = None,
    lightweight_review_model: LightweightReviewModelOption = None,
    lightweight_review_reasoning_effort: LightweightReviewReasoningEffortOption = None,
    pr_url: PrUrlOption = None,
    review_own: ReviewOwnOption = False,
    use_saved_review: UseSavedReviewOption = False,
    output_format: OutputFormatOption = "text",
) -> None:
    """Run one polling cycle."""
    if output_format not in ("text", "json"):
        raise typer.BadParameter(f"Invalid --output-format: {output_format}. Use 'text' or 'json'.")
    if output_format == "json" and not pr_url:
        raise typer.BadParameter("--output-format json requires at least one --pr-url.")
    if output_format == "json":
        redirect_to_stderr()
    target_pr_urls = _target_pr_urls_for_run_once(
        pr_url,
        use_saved_review=use_saved_review,
    )
    cfg, store = _load_runtime(
        config,
        enabled_reviewer,
        codex_backend,
        claude_backend,
        claude_model,
        claude_reasoning_effort,
        reconciler_backend,
        reconciler_model,
        reconciler_reasoning_effort,
        codex_model,
        codex_reasoning_effort,
        auto_post_review,
        gemini_model,
        gemini_fallback_model,
        slash_command_enabled,
        triage_backend,
        triage_model,
        lightweight_review_backend,
        lightweight_review_model,
        lightweight_review_reasoning_effort,
    )
    if review_own:
        cfg = _apply_bool_override(cfg, "skip_own_prs", False, "--review-own")
    results: list[ProcessingResult] = []
    try:
        refresh_github_token()
        preflight = run_preflight(cfg)
        if target_pr_urls:
            client = GitHubClient(viewer_login=preflight.viewer_login)
            workspace_mgr = PRWorkspace(Path(cfg.clone_root))

            async def _run_targeted() -> list[ProcessingResult]:
                run_results: list[ProcessingResult] = []
                total = len(target_pr_urls)
                for index, url in enumerate(target_pr_urls, start=1):
                    info(f"PR {index}/{total}: {url}")
                    candidate = client.get_pr_candidate(url)
                    if total > 1 and index > 1:
                        ahead = index - 1
                        try:
                            client.post_pr_comment_inline(
                                candidate,
                                f"Queued for review (position {index} of {total}, {ahead} ahead).",
                            )
                        except Exception as exc:  # noqa: BLE001
                            warn(f"{candidate.key}: failed to post queue position: {exc}")
                    result = await process_candidate(
                        cfg,
                        client,
                        store,
                        workspace_mgr,
                        candidate,
                        use_saved_review=use_saved_review,
                    )
                    run_results.append(result)
                return run_results

            results = asyncio.run(_run_targeted())
            processed = sum(1 for r in results if r.processed)
        else:
            _require_github_orgs(cfg)
            processed = asyncio.run(run_cycle(cfg, preflight, store))

        if output_format == "json":
            print(json.dumps([r.to_dict() for r in results], indent=2))
        else:
            info(f"run-once finished. Processed {processed} PR(s)")
    finally:
        store.release_lock()


@app.command("start")
def start_command(
    config: ConfigOption = Path("config.toml"),
    enabled_reviewer: EnabledReviewerOption = None,
    codex_backend: CodexBackendOption = None,
    claude_backend: ClaudeBackendOption = None,
    claude_model: ClaudeModelOption = None,
    claude_reasoning_effort: ClaudeReasoningEffortOption = None,
    reconciler_backend: ReconcilerBackendOption = None,
    reconciler_model: ReconcilerModelOption = None,
    reconciler_reasoning_effort: ReconcilerReasoningEffortOption = None,
    codex_model: CodexModelOption = None,
    codex_reasoning_effort: CodexReasoningEffortOption = None,
    auto_post_review: AutoPostReviewOption = None,
    gemini_model: GeminiModelOption = None,
    gemini_fallback_model: GeminiFallbackModelOption = None,
    slash_command_enabled: SlashCommandEnabledOption = None,
    triage_backend: TriageBackendOption = None,
    triage_model: TriageModelOption = None,
    lightweight_review_backend: LightweightReviewBackendOption = None,
    lightweight_review_model: LightweightReviewModelOption = None,
    lightweight_review_reasoning_effort: LightweightReviewReasoningEffortOption = None,
    web_port: Annotated[
        int | None,
        typer.Option(
            "--web-port", help="Port for the history web UI. Enables embedded web server."
        ),  # noqa: E501
    ] = None,
) -> None:
    """Run daemon forever."""
    cfg, store = _load_runtime(
        config,
        enabled_reviewer,
        codex_backend,
        claude_backend,
        claude_model,
        claude_reasoning_effort,
        reconciler_backend,
        reconciler_model,
        reconciler_reasoning_effort,
        codex_model,
        codex_reasoning_effort,
        auto_post_review,
        gemini_model,
        gemini_fallback_model,
        slash_command_enabled,
        triage_backend,
        triage_model,
        lightweight_review_backend,
        lightweight_review_model,
        lightweight_review_reasoning_effort,
    )
    _require_github_orgs(cfg)

    def reload_config() -> AppConfig:
        return _load_config_with_overrides(
            config,
            enabled_reviewer,
            codex_backend,
            claude_backend,
            claude_model,
            claude_reasoning_effort,
            reconciler_backend,
            reconciler_model,
            reconciler_reasoning_effort,
            codex_model,
            codex_reasoning_effort,
            auto_post_review,
            gemini_model,
            gemini_fallback_model,
            slash_command_enabled,
            triage_backend,
            triage_model,
            lightweight_review_backend,
            lightweight_review_model,
            lightweight_review_reasoning_effort,
        )

    try:
        refresh_github_token()
        preflight = run_preflight(cfg)
        if web_port is not None:
            import uvicorn

            from code_reviewer.daemon import create_daemon_app

            resolved_static = _resolve_static_dir()
            app = create_daemon_app(
                config=cfg,
                preflight=preflight,
                store=store,
                reviews_dir=Path("./reviews"),
                static_dir=resolved_static,
                reload_config=reload_config,
            )
            info(f"Starting daemon with web UI on port {web_port}")
            uvicorn.run(app, host="0.0.0.0", port=web_port, log_level="warning")
        else:
            asyncio.run(start_daemon(cfg, preflight, store, reload_config=reload_config))
    except KeyboardInterrupt:
        info("Shutting down daemon")
    except Exception as exc:  # noqa: BLE001
        error(str(exc))
        raise typer.Exit(code=1) from exc
    finally:
        info("Released state lock")
        store.release_lock()


RepoOption = Annotated[
    Path,
    typer.Option(
        "--repo",
        help="Path to local git repository. Defaults to current directory.",
    ),
]
BaseOption = Annotated[
    str | None,
    typer.Option(
        "--base",
        help="Base branch or ref to diff against (required for branch mode).",
    ),
]
BranchOption = Annotated[
    str | None,
    typer.Option(
        "--branch",
        help="Head branch to review. Defaults to current branch when --base is provided.",
    ),
]
UncommittedOption = Annotated[
    bool,
    typer.Option(
        "--uncommitted",
        help="Review uncommitted changes (staged + unstaged) against HEAD.",
    ),
]
CommitOption = Annotated[
    str | None,
    typer.Option(
        "--commit",
        help="Review a specific commit (diffs against its parent).",
    ),
]


def _load_config_with_reviewer_overrides(
    config_path: Path | None,
    enabled_reviewer: list[str] | None,
    codex_backend: str | None,
    claude_backend: str | None,
    claude_model: str | None,
    claude_reasoning_effort: str | None,
    reconciler_backend: str | None,
    reconciler_model: str | None,
    reconciler_reasoning_effort: str | None,
    codex_model: str | None,
    codex_reasoning_effort: str | None,
    gemini_model: str | None,
    gemini_fallback_model: str | None,
    triage_backend: str | None,
    triage_model: str | None,
    lightweight_review_backend: str | None,
    lightweight_review_model: str | None,
    lightweight_review_reasoning_effort: str | None,
) -> AppConfig:
    cfg = _load_config_or_default(config_path)
    cfg = _apply_enabled_reviewer_override(cfg, enabled_reviewer)
    cfg = _apply_codex_backend_override(cfg, codex_backend)
    cfg = _apply_claude_backend_override(cfg, claude_backend)
    cfg = _apply_field_override(cfg, "claude_model", claude_model, "--claude-model")
    cfg = _apply_field_override(
        cfg,
        "claude_reasoning_effort",
        claude_reasoning_effort,
        "--claude-reasoning-effort",
    )
    cfg = _apply_field_override(
        cfg,
        "reconciler_backend",
        reconciler_backend,
        "--reconciler-backend",
    )
    cfg = _apply_field_override(cfg, "reconciler_model", reconciler_model, "--reconciler-model")
    cfg = _apply_field_override(
        cfg,
        "reconciler_reasoning_effort",
        reconciler_reasoning_effort,
        "--reconciler-reasoning-effort",
    )
    cfg = _apply_field_override(cfg, "codex_model", codex_model, "--codex-model")
    cfg = _apply_field_override(
        cfg,
        "codex_reasoning_effort",
        codex_reasoning_effort,
        "--codex-reasoning-effort",
    )
    cfg = _apply_field_override(cfg, "gemini_model", gemini_model, "--gemini-model")
    cfg = _apply_field_override(
        cfg, "gemini_fallback_model", gemini_fallback_model, "--gemini-fallback-model"
    )
    cfg = _apply_field_override(cfg, "triage_backend", triage_backend, "--triage-backend")
    cfg = _apply_field_override(cfg, "triage_model", triage_model, "--triage-model")
    cfg = _apply_field_override(
        cfg,
        "lightweight_review_backend",
        lightweight_review_backend,
        "--lightweight-review-backend",
    )
    cfg = _apply_field_override(
        cfg,
        "lightweight_review_model",
        lightweight_review_model,
        "--lightweight-review-model",
    )
    cfg = _apply_field_override(
        cfg,
        "lightweight_review_reasoning_effort",
        lightweight_review_reasoning_effort,
        "--lightweight-review-reasoning-effort",
    )
    return cfg


@app.command("review")
def review_command(
    config: OptionalConfigOption = None,
    repo: RepoOption = Path("."),
    base: BaseOption = None,
    branch: BranchOption = None,
    uncommitted: UncommittedOption = False,
    commit: CommitOption = None,
    output_format: OutputFormatOption = "text",
    enabled_reviewer: EnabledReviewerOption = None,
    codex_backend: CodexBackendOption = None,
    claude_backend: ClaudeBackendOption = None,
    claude_model: ClaudeModelOption = None,
    claude_reasoning_effort: ClaudeReasoningEffortOption = None,
    reconciler_backend: ReconcilerBackendOption = None,
    reconciler_model: ReconcilerModelOption = None,
    reconciler_reasoning_effort: ReconcilerReasoningEffortOption = None,
    codex_model: CodexModelOption = None,
    codex_reasoning_effort: CodexReasoningEffortOption = None,
    gemini_model: GeminiModelOption = None,
    gemini_fallback_model: GeminiFallbackModelOption = None,
    triage_backend: TriageBackendOption = None,
    triage_model: TriageModelOption = None,
    lightweight_review_backend: LightweightReviewBackendOption = None,
    lightweight_review_model: LightweightReviewModelOption = None,
    lightweight_review_reasoning_effort: LightweightReviewReasoningEffortOption = None,
) -> None:
    """Review local changes: branch vs branch, uncommitted changes, or a specific commit."""
    if output_format not in ("text", "json"):
        raise typer.BadParameter(f"Invalid --output-format: {output_format}. Use 'text' or 'json'.")

    # Determine review mode
    mode_count = sum(
        [
            base is not None or branch is not None,
            uncommitted,
            commit is not None,
        ]
    )
    if mode_count == 0:
        raise typer.BadParameter(
            "Specify a review mode: --base/--branch (branch comparison), "
            "--uncommitted, or --commit SHA."
        )
    branch_only = base is not None and branch is not None and not uncommitted and commit is None
    if mode_count > 1 and not branch_only:
        raise typer.BadParameter(
            "--uncommitted and --commit are mutually exclusive with each other "
            "and with --base/--branch."
        )

    if uncommitted:
        mode = "uncommitted"
    elif commit is not None:
        mode = "commit"
    else:
        mode = "branch"
        if base is None:
            raise typer.BadParameter("--base is required for branch comparison mode.")

    if output_format == "json":
        redirect_to_stderr()

    repo_path = repo.resolve()
    try:
        validate_git_repo(repo_path)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    cfg = _load_config_with_reviewer_overrides(
        config,
        enabled_reviewer,
        codex_backend,
        claude_backend,
        claude_model,
        claude_reasoning_effort,
        reconciler_backend,
        reconciler_model,
        reconciler_reasoning_effort,
        codex_model,
        codex_reasoning_effort,
        gemini_model,
        gemini_fallback_model,
        triage_backend,
        triage_model,
        lightweight_review_backend,
        lightweight_review_model,
        lightweight_review_reasoning_effort,
    )

    try:
        base_ref, head_ref = resolve_diff_refs(
            repo_path,
            mode=mode,
            base=base,
            branch=branch,
            commit=commit,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if head_ref == "WORKING_TREE":
        head_sha = resolve_head_sha(repo_path, "HEAD")
    else:
        head_sha = resolve_head_sha(repo_path, head_ref)

    additions, deletions, changed_files = gather_diff_metadata(
        repo_path,
        base_ref,
        head_ref,
    )

    if not changed_files:
        info("No changes detected between the specified refs.")
        if output_format == "json":
            print(json.dumps({"processed": False, "status": "no_changes"}, indent=2))
        return

    candidate = build_local_candidate(
        repo_path,
        mode=mode,
        base_ref=base_ref,
        head_ref=head_ref,
        head_sha=head_sha,
        additions=additions,
        deletions=deletions,
        changed_file_paths=changed_files,
    )

    info(
        f"Reviewing {candidate.title} "
        f"({additions}+/{deletions}- across {len(changed_files)} file(s))"
    )

    result = asyncio.run(process_local_review(cfg, candidate, repo_path))

    if output_format == "json":
        print(json.dumps(result.to_dict(), indent=2))
    elif result.processed:
        info("Review complete.")
        if result.final_review:
            console.print()
            console.print(result.final_review)
    else:
        error(f"Review failed: {result.error or result.status}")


WebhookHostOption = Annotated[
    str | None,
    typer.Option("--host", help="Host to bind the webhook server to. Env: WEBHOOK_HOST."),
]
WebhookPortOption = Annotated[
    int | None,
    typer.Option("--port", "-p", help="Port to bind the webhook server to. Env: WEBHOOK_PORT."),
]


@app.command("webhook")
def webhook_command(
    host: WebhookHostOption = None,
    port: WebhookPortOption = None,
) -> None:
    """Run GitHub App webhook server.

    Listens for pull_request and issue_comment events, validates the webhook
    signature, and spawns ``code-reviewer run-once --pr-url`` for each
    actionable event.

    Configuration is read from environment variables:
      WEBHOOK_SECRET  — GitHub App webhook secret (recommended)
      WEBHOOK_HOST    — Bind address (default: 0.0.0.0)
      WEBHOOK_PORT    — Bind port (default: 8000)
    """
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = WebhookConfig.from_env()
    if host is not None:
        cfg.host = host
    if port is not None:
        cfg.port = port
    if not cfg.webhook_secret:
        warn("WEBHOOK_SECRET is not set; signature validation is disabled")
    info(f"Starting webhook server on {cfg.host}:{cfg.port}")
    run_server(cfg)


def _resolve_static_dir() -> Path | None:
    default_static = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
    return default_static if default_static.is_dir() else None


HistoryHostOption = Annotated[
    str,
    typer.Option("--host", help="Host to bind the history server to."),
]
HistoryPortOption = Annotated[
    int,
    typer.Option("--port", "-p", help="Port to bind the history server to."),
]
HistoryReviewsDirOption = Annotated[
    Path,
    typer.Option("--reviews-dir", help="Path to reviews directory."),
]
HistoryDevOption = Annotated[
    bool,
    typer.Option("--dev", help="Enable CORS for development (React dev server on another port)."),
]
HistoryStaticDirOption = Annotated[
    Path | None,
    typer.Option("--static-dir", help="Path to built React app directory."),
]


@app.command("history")
def history_command(
    host: HistoryHostOption = "127.0.0.1",
    port: HistoryPortOption = 8080,
    reviews_dir: HistoryReviewsDirOption = Path("./reviews"),
    dev: HistoryDevOption = False,
    static_dir: HistoryStaticDirOption = None,
) -> None:
    """Browse PR review history in a web UI.

    Starts an HTTP server that serves a JSON API for browsing review
    artifacts and (optionally) a built React frontend.
    """
    import logging

    import uvicorn

    from code_reviewer.history_server import create_history_app

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    resolved_static = static_dir
    if resolved_static is None:
        resolved_static = _resolve_static_dir()
    info(f"Starting history server on {host}:{port}")
    info(f"Reviews directory: {reviews_dir.resolve()}")
    if resolved_static:
        info(f"Serving frontend from: {resolved_static.resolve()}")
    elif not dev:
        warn("No static directory found. Run 'npm run build' in web/ to enable the frontend.")
    app = create_history_app(
        reviews_dir=reviews_dir,
        static_dir=resolved_static,
        enable_cors=dev,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
