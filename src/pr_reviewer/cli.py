from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.table import Table

from pr_reviewer.config import AppConfig, load_config
from pr_reviewer.daemon import run_cycle, start_daemon
from pr_reviewer.github import GitHubClient
from pr_reviewer.local_review import (
    build_local_candidate,
    gather_diff_metadata,
    resolve_diff_refs,
    resolve_head_sha,
    validate_git_repo,
)
from pr_reviewer.logger import console, error, info, redirect_to_stderr
from pr_reviewer.models import ProcessingResult
from pr_reviewer.preflight import run_preflight
from pr_reviewer.processor import process_candidate, process_local_review
from pr_reviewer.state import StateStore
from pr_reviewer.workspace import PRWorkspace

app = typer.Typer(add_completion=False, help="PR review daemon")
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
EnabledReviewerOption = Annotated[
    list[str] | None,
    typer.Option(
        "--enabled-reviewer",
        "-r",
        help=(
            "Override enabled_reviewers from config. "
            "Repeat flag to enable multiple reviewers."
        ),
    ),
]
CodexBackendOption = Annotated[
    str | None,
    typer.Option(
        "--codex-backend",
        help=(
            "Override codex_backend from config. "
            "Allowed: cli, agents_sdk."
        ),
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
        help=(
            "Override reconciler_reasoning_effort from config. "
            "Allowed: low, medium, high, max."
        ),
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


def _resolve_reconciler_settings(config: AppConfig) -> tuple[str, str | None, str | None]:
    backend = config.reconciler_backend
    if backend == "claude":
        model = config.reconciler_model or config.claude_model
        reasoning_effort = config.reconciler_reasoning_effort or config.claude_reasoning_effort
    elif backend == "codex":
        model = config.reconciler_model or config.codex_model
        reasoning_effort = config.reconciler_reasoning_effort or config.codex_reasoning_effort
    else:
        model = config.reconciler_model or config.gemini_model
        reasoning_effort = None
    return backend, model, reasoning_effort


def _load_runtime(
    config_path: Path,
    enabled_reviewer: list[str] | None,
    codex_backend: str | None,
    claude_model: str | None,
    claude_reasoning_effort: str | None,
    reconciler_backend: str | None,
    reconciler_model: str | None,
    reconciler_reasoning_effort: str | None,
    codex_model: str | None,
    codex_reasoning_effort: str | None,
    auto_post_review: bool | None,
    gemini_model: str | None,
    slash_command_enabled: bool | None,
    triage_backend: str | None,
    triage_model: str | None,
    lightweight_review_backend: str | None,
    lightweight_review_model: str | None,
    lightweight_review_reasoning_effort: str | None,
) -> tuple[AppConfig, StateStore]:
    config = load_config(config_path)
    config = _apply_enabled_reviewer_override(config, enabled_reviewer)
    config = _apply_codex_backend_override(config, codex_backend)
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
    claude_model: ClaudeModelOption = None,
    claude_reasoning_effort: ClaudeReasoningEffortOption = None,
    reconciler_backend: ReconcilerBackendOption = None,
    reconciler_model: ReconcilerModelOption = None,
    reconciler_reasoning_effort: ReconcilerReasoningEffortOption = None,
    codex_model: CodexModelOption = None,
    codex_reasoning_effort: CodexReasoningEffortOption = None,
    auto_post_review: AutoPostReviewOption = None,
    gemini_model: GeminiModelOption = None,
    slash_command_enabled: SlashCommandEnabledOption = None,
    triage_backend: TriageBackendOption = None,
    triage_model: TriageModelOption = None,
    lightweight_review_backend: LightweightReviewBackendOption = None,
    lightweight_review_model: LightweightReviewModelOption = None,
    lightweight_review_reasoning_effort: LightweightReviewReasoningEffortOption = None,
) -> None:
    """Run preflight checks and print runtime summary."""
    cfg = load_config(config)
    cfg = _apply_enabled_reviewer_override(cfg, enabled_reviewer)
    cfg = _apply_codex_backend_override(cfg, codex_backend)
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
    preflight = run_preflight(cfg)

    table = Table(title="pr-reviewer check")
    table.add_column("Item")
    table.add_column("Value")
    table.add_row("GitHub owners", ", ".join(cfg.github_owners))
    table.add_row("Viewer", preflight.viewer_login)
    table.add_row("Poll interval", str(cfg.poll_interval_seconds))
    table.add_row("Auto post", str(cfg.auto_post_review))
    table.add_row("Auto submit decision", str(cfg.auto_submit_review_decision))
    table.add_row("Include reviewer stderr", str(cfg.include_reviewer_stderr))
    table.add_row("Enabled reviewers", ", ".join(cfg.enabled_reviewers))
    table.add_row("Claude model", cfg.claude_model or "default")
    table.add_row("Claude reasoning effort", cfg.claude_reasoning_effort or "default")
    reconciler_backend_value, reconciler_model_value, reconciler_effort_value = (
        _resolve_reconciler_settings(cfg)
    )
    reconciler_effort_display = (
        reconciler_effort_value or "default"
        if reconciler_backend_value != "gemini"
        else "n/a"
    )
    table.add_row("Reconciler backend", reconciler_backend_value)
    table.add_row("Reconciler model", reconciler_model_value or "default")
    table.add_row("Reconciler reasoning effort", reconciler_effort_display)
    table.add_row("Codex backend", cfg.codex_backend)
    table.add_row("Codex model", cfg.codex_model)
    table.add_row("Codex reasoning effort", cfg.codex_reasoning_effort or "default")
    table.add_row("Gemini model", cfg.gemini_model or "default")
    table.add_row("Slash command enabled", str(cfg.slash_command_enabled))
    table.add_row("Triage backend", cfg.triage_backend)
    table.add_row("Triage model", cfg.triage_model or "default")
    table.add_row("Triage timeout", str(cfg.triage_timeout_seconds))
    table.add_row("Lightweight review backend", cfg.lightweight_review_backend)
    table.add_row("Lightweight review model", cfg.lightweight_review_model or "default")
    lw_effort = cfg.lightweight_review_reasoning_effort or "default"
    table.add_row("Lightweight review reasoning effort", lw_effort)
    table.add_row("Lightweight review timeout", str(cfg.lightweight_review_timeout_seconds))
    table.add_row("Trigger mode", cfg.trigger_mode)
    table.add_row("Output dir", str(Path(cfg.output_dir).resolve()))
    table.add_row("State file", str(Path(cfg.state_file).resolve()))
    console.print(table)


@app.command("run-once")
def run_once_command(
    config: ConfigOption = Path("config.toml"),
    enabled_reviewer: EnabledReviewerOption = None,
    codex_backend: CodexBackendOption = None,
    claude_model: ClaudeModelOption = None,
    claude_reasoning_effort: ClaudeReasoningEffortOption = None,
    reconciler_backend: ReconcilerBackendOption = None,
    reconciler_model: ReconcilerModelOption = None,
    reconciler_reasoning_effort: ReconcilerReasoningEffortOption = None,
    codex_model: CodexModelOption = None,
    codex_reasoning_effort: CodexReasoningEffortOption = None,
    auto_post_review: AutoPostReviewOption = None,
    gemini_model: GeminiModelOption = None,
    slash_command_enabled: SlashCommandEnabledOption = None,
    triage_backend: TriageBackendOption = None,
    triage_model: TriageModelOption = None,
    lightweight_review_backend: LightweightReviewBackendOption = None,
    lightweight_review_model: LightweightReviewModelOption = None,
    lightweight_review_reasoning_effort: LightweightReviewReasoningEffortOption = None,
    pr_url: PrUrlOption = None,
    use_saved_review: UseSavedReviewOption = False,
    output_format: OutputFormatOption = "text",
) -> None:
    """Run one polling cycle."""
    if output_format not in ("text", "json"):
        raise typer.BadParameter(
            f"Invalid --output-format: {output_format}. Use 'text' or 'json'."
        )
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
        claude_model,
        claude_reasoning_effort,
        reconciler_backend,
        reconciler_model,
        reconciler_reasoning_effort,
        codex_model,
        codex_reasoning_effort,
        auto_post_review,
        gemini_model,
        slash_command_enabled,
        triage_backend,
        triage_model,
        lightweight_review_backend,
        lightweight_review_model,
        lightweight_review_reasoning_effort,
    )
    results: list[ProcessingResult] = []
    try:
        preflight = run_preflight(cfg)
        if target_pr_urls:
            client = GitHubClient(viewer_login=preflight.viewer_login)
            workspace_mgr = PRWorkspace(Path(cfg.clone_root))

            async def _run_targeted() -> list[ProcessingResult]:
                run_results: list[ProcessingResult] = []
                for index, url in enumerate(target_pr_urls, start=1):
                    info(f"PR {index}/{len(target_pr_urls)}: {url}")
                    candidate = client.get_pr_candidate(url)
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
    claude_model: ClaudeModelOption = None,
    claude_reasoning_effort: ClaudeReasoningEffortOption = None,
    reconciler_backend: ReconcilerBackendOption = None,
    reconciler_model: ReconcilerModelOption = None,
    reconciler_reasoning_effort: ReconcilerReasoningEffortOption = None,
    codex_model: CodexModelOption = None,
    codex_reasoning_effort: CodexReasoningEffortOption = None,
    auto_post_review: AutoPostReviewOption = None,
    gemini_model: GeminiModelOption = None,
    slash_command_enabled: SlashCommandEnabledOption = None,
    triage_backend: TriageBackendOption = None,
    triage_model: TriageModelOption = None,
    lightweight_review_backend: LightweightReviewBackendOption = None,
    lightweight_review_model: LightweightReviewModelOption = None,
    lightweight_review_reasoning_effort: LightweightReviewReasoningEffortOption = None,
) -> None:
    """Run daemon forever."""
    cfg, store = _load_runtime(
        config,
        enabled_reviewer,
        codex_backend,
        claude_model,
        claude_reasoning_effort,
        reconciler_backend,
        reconciler_model,
        reconciler_reasoning_effort,
        codex_model,
        codex_reasoning_effort,
        auto_post_review,
        gemini_model,
        slash_command_enabled,
        triage_backend,
        triage_model,
        lightweight_review_backend,
        lightweight_review_model,
        lightweight_review_reasoning_effort,
    )
    try:
        preflight = run_preflight(cfg)
        asyncio.run(start_daemon(cfg, preflight, store))
    except KeyboardInterrupt:
        info("Shutting down daemon")
    except Exception as exc:  # noqa: BLE001
        error(str(exc))
        raise typer.Exit(code=1) from exc
    finally:
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
    config_path: Path,
    enabled_reviewer: list[str] | None,
    codex_backend: str | None,
    claude_model: str | None,
    claude_reasoning_effort: str | None,
    reconciler_backend: str | None,
    reconciler_model: str | None,
    reconciler_reasoning_effort: str | None,
    codex_model: str | None,
    codex_reasoning_effort: str | None,
    gemini_model: str | None,
    triage_backend: str | None,
    triage_model: str | None,
    lightweight_review_backend: str | None,
    lightweight_review_model: str | None,
    lightweight_review_reasoning_effort: str | None,
) -> AppConfig:
    cfg = load_config(config_path)
    cfg = _apply_enabled_reviewer_override(cfg, enabled_reviewer)
    cfg = _apply_codex_backend_override(cfg, codex_backend)
    cfg = _apply_field_override(cfg, "claude_model", claude_model, "--claude-model")
    cfg = _apply_field_override(
        cfg, "claude_reasoning_effort", claude_reasoning_effort, "--claude-reasoning-effort",
    )
    cfg = _apply_field_override(
        cfg, "reconciler_backend", reconciler_backend, "--reconciler-backend",
    )
    cfg = _apply_field_override(cfg, "reconciler_model", reconciler_model, "--reconciler-model")
    cfg = _apply_field_override(
        cfg, "reconciler_reasoning_effort", reconciler_reasoning_effort,
        "--reconciler-reasoning-effort",
    )
    cfg = _apply_field_override(cfg, "codex_model", codex_model, "--codex-model")
    cfg = _apply_field_override(
        cfg, "codex_reasoning_effort", codex_reasoning_effort, "--codex-reasoning-effort",
    )
    cfg = _apply_field_override(cfg, "gemini_model", gemini_model, "--gemini-model")
    cfg = _apply_field_override(cfg, "triage_backend", triage_backend, "--triage-backend")
    cfg = _apply_field_override(cfg, "triage_model", triage_model, "--triage-model")
    cfg = _apply_field_override(
        cfg, "lightweight_review_backend", lightweight_review_backend,
        "--lightweight-review-backend",
    )
    cfg = _apply_field_override(
        cfg, "lightweight_review_model", lightweight_review_model, "--lightweight-review-model",
    )
    cfg = _apply_field_override(
        cfg, "lightweight_review_reasoning_effort", lightweight_review_reasoning_effort,
        "--lightweight-review-reasoning-effort",
    )
    return cfg


@app.command("review")
def review_command(
    config: ConfigOption = Path("config.toml"),
    repo: RepoOption = Path("."),
    base: BaseOption = None,
    branch: BranchOption = None,
    uncommitted: UncommittedOption = False,
    commit: CommitOption = None,
    output_format: OutputFormatOption = "text",
    enabled_reviewer: EnabledReviewerOption = None,
    codex_backend: CodexBackendOption = None,
    claude_model: ClaudeModelOption = None,
    claude_reasoning_effort: ClaudeReasoningEffortOption = None,
    reconciler_backend: ReconcilerBackendOption = None,
    reconciler_model: ReconcilerModelOption = None,
    reconciler_reasoning_effort: ReconcilerReasoningEffortOption = None,
    codex_model: CodexModelOption = None,
    codex_reasoning_effort: CodexReasoningEffortOption = None,
    gemini_model: GeminiModelOption = None,
    triage_backend: TriageBackendOption = None,
    triage_model: TriageModelOption = None,
    lightweight_review_backend: LightweightReviewBackendOption = None,
    lightweight_review_model: LightweightReviewModelOption = None,
    lightweight_review_reasoning_effort: LightweightReviewReasoningEffortOption = None,
) -> None:
    """Review local changes: branch vs branch, uncommitted changes, or a specific commit."""
    if output_format not in ("text", "json"):
        raise typer.BadParameter(
            f"Invalid --output-format: {output_format}. Use 'text' or 'json'."
        )

    # Determine review mode
    mode_count = sum([
        base is not None or branch is not None,
        uncommitted,
        commit is not None,
    ])
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
        config, enabled_reviewer, codex_backend,
        claude_model, claude_reasoning_effort,
        reconciler_backend, reconciler_model, reconciler_reasoning_effort,
        codex_model, codex_reasoning_effort, gemini_model,
        triage_backend, triage_model,
        lightweight_review_backend, lightweight_review_model,
        lightweight_review_reasoning_effort,
    )

    try:
        base_ref, head_ref = resolve_diff_refs(
            repo_path, mode=mode, base=base, branch=branch, commit=commit,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if head_ref == "WORKING_TREE":
        head_sha = resolve_head_sha(repo_path, "HEAD")
    else:
        head_sha = resolve_head_sha(repo_path, head_ref)

    additions, deletions, changed_files = gather_diff_metadata(
        repo_path, base_ref, head_ref,
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
        info(f"Review complete. Output: {result.output_file}")
        if result.final_review:
            console.print()
            console.print(result.final_review)
    else:
        error(f"Review failed: {result.error or result.status}")
