from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.table import Table

from pr_reviewer.config import AppConfig, load_config
from pr_reviewer.daemon import run_cycle, start_daemon
from pr_reviewer.github import GitHubClient
from pr_reviewer.logger import console, error, info
from pr_reviewer.preflight import run_preflight
from pr_reviewer.processor import process_candidate
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
AutoPostReviewOption = Annotated[
    bool | None,
    typer.Option(
        "--auto-post-review/--no-auto-post-review",
        help="Override auto_post_review from config.",
    ),
]
ForceOption = Annotated[
    bool,
    typer.Option(
        "--force",
        help="Bypass skip checks when reviewing specific PR URL(s) via --pr-url.",
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
IgnoreSavedReviewOption = Annotated[
    bool,
    typer.Option(
        "--ignore-saved-review",
        help="Bypass the saved-review dedupe check for targeted PR URL(s).",
    ),
]
IgnoreExistingCommentOption = Annotated[
    bool,
    typer.Option(
        "--ignore-existing-comment",
        help="Bypass the existing-issue-comment check for targeted PR URL(s).",
    ),
]
IgnoreHeadShaOption = Annotated[
    bool,
    typer.Option(
        "--ignore-head-sha",
        help="Bypass the head-SHA dedupe check for targeted PR URL(s).",
    ),
]
PrUrlOption = Annotated[
    list[str] | None,
    typer.Option(
        "--pr-url",
        help="GitHub pull request URL to review directly. Repeat for multiple PRs.",
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


def _load_runtime(
    config_path: Path,
    enabled_reviewer: list[str] | None,
    codex_backend: str | None,
    claude_model: str | None,
    claude_reasoning_effort: str | None,
    codex_model: str | None,
    codex_reasoning_effort: str | None,
    auto_post_review: bool | None,
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
    store = StateStore(Path(config.state_file))
    store.acquire_lock()
    store.load()
    return config, store


def _target_pr_urls_for_run_once(
    pr_url: list[str] | None,
    *,
    force: bool,
    use_saved_review: bool,
    ignore_saved_review: bool,
    ignore_existing_comment: bool,
    ignore_head_sha: bool,
) -> list[str]:
    deduped_urls = list(dict.fromkeys(pr_url or []))
    if force and not deduped_urls:
        raise typer.BadParameter("--force requires at least one --pr-url value.")
    if use_saved_review and not deduped_urls:
        raise typer.BadParameter("--use-saved-review requires at least one --pr-url value.")
    if ignore_saved_review and not deduped_urls:
        raise typer.BadParameter("--ignore-saved-review requires at least one --pr-url value.")
    if ignore_existing_comment and not deduped_urls:
        raise typer.BadParameter("--ignore-existing-comment requires at least one --pr-url value.")
    if ignore_head_sha and not deduped_urls:
        raise typer.BadParameter("--ignore-head-sha requires at least one --pr-url value.")
    if use_saved_review and force:
        raise typer.BadParameter("--use-saved-review cannot be combined with --force.")
    if use_saved_review and ignore_saved_review:
        raise typer.BadParameter(
            "--use-saved-review cannot be combined with --ignore-saved-review."
        )
    return deduped_urls


def _resolve_skip_overrides(
    *,
    force: bool,
    ignore_saved_review: bool,
    ignore_existing_comment: bool,
    ignore_head_sha: bool,
) -> tuple[bool, bool, bool]:
    return (
        force or ignore_saved_review,
        force or ignore_existing_comment,
        force or ignore_head_sha,
    )


@app.command("check")
def check_command(
    config: ConfigOption = Path("config.toml"),
    enabled_reviewer: EnabledReviewerOption = None,
    codex_backend: CodexBackendOption = None,
    claude_model: ClaudeModelOption = None,
    claude_reasoning_effort: ClaudeReasoningEffortOption = None,
    codex_model: CodexModelOption = None,
    codex_reasoning_effort: CodexReasoningEffortOption = None,
    auto_post_review: AutoPostReviewOption = None,
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
    preflight = run_preflight(cfg)

    table = Table(title="pr-reviewer check")
    table.add_column("Item")
    table.add_column("Value")
    table.add_row("GitHub org", cfg.github_org)
    table.add_row("Viewer", preflight.viewer_login)
    table.add_row("Poll interval", str(cfg.poll_interval_seconds))
    table.add_row("Auto post", str(cfg.auto_post_review))
    table.add_row("Auto submit decision", str(cfg.auto_submit_review_decision))
    table.add_row("Include reviewer stderr", str(cfg.include_reviewer_stderr))
    table.add_row("Enabled reviewers", ", ".join(cfg.enabled_reviewers))
    table.add_row("Claude model", cfg.claude_model or "default")
    table.add_row("Claude reasoning effort", cfg.claude_reasoning_effort or "default")
    table.add_row("Codex backend", cfg.codex_backend)
    table.add_row("Codex model", cfg.codex_model)
    table.add_row("Codex reasoning effort", cfg.codex_reasoning_effort or "default")
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
    codex_model: CodexModelOption = None,
    codex_reasoning_effort: CodexReasoningEffortOption = None,
    auto_post_review: AutoPostReviewOption = None,
    force: ForceOption = False,
    pr_url: PrUrlOption = None,
    use_saved_review: UseSavedReviewOption = False,
    ignore_saved_review: IgnoreSavedReviewOption = False,
    ignore_existing_comment: IgnoreExistingCommentOption = False,
    ignore_head_sha: IgnoreHeadShaOption = False,
) -> None:
    """Run one polling cycle."""
    target_pr_urls = _target_pr_urls_for_run_once(
        pr_url,
        force=force,
        use_saved_review=use_saved_review,
        ignore_saved_review=ignore_saved_review,
        ignore_existing_comment=ignore_existing_comment,
        ignore_head_sha=ignore_head_sha,
    )
    (
        resolved_ignore_saved_review,
        resolved_ignore_existing_comment,
        resolved_ignore_head_sha,
    ) = _resolve_skip_overrides(
        force=force,
        ignore_saved_review=ignore_saved_review,
        ignore_existing_comment=ignore_existing_comment,
        ignore_head_sha=ignore_head_sha,
    )
    cfg, store = _load_runtime(
        config,
        enabled_reviewer,
        codex_backend,
        claude_model,
        claude_reasoning_effort,
        codex_model,
        codex_reasoning_effort,
        auto_post_review,
    )
    try:
        preflight = run_preflight(cfg)
        if target_pr_urls:
            client = GitHubClient(viewer_login=preflight.viewer_login)
            workspace_mgr = PRWorkspace(Path(cfg.clone_root))

            async def _run_targeted() -> int:
                processed = 0
                for index, url in enumerate(target_pr_urls, start=1):
                    info(f"PR {index}/{len(target_pr_urls)}: {url}")
                    candidate = client.get_pr_candidate(url)
                    changed = await process_candidate(
                        cfg,
                        client,
                        store,
                        workspace_mgr,
                        candidate,
                        use_saved_review=use_saved_review,
                        ignore_saved_review=resolved_ignore_saved_review,
                        ignore_existing_comment=resolved_ignore_existing_comment,
                        ignore_head_sha=resolved_ignore_head_sha,
                    )
                    if changed:
                        processed += 1
                return processed

            processed = asyncio.run(_run_targeted())
        else:
            processed = asyncio.run(run_cycle(cfg, preflight, store))
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
    codex_model: CodexModelOption = None,
    codex_reasoning_effort: CodexReasoningEffortOption = None,
    auto_post_review: AutoPostReviewOption = None,
) -> None:
    """Run daemon forever."""
    cfg, store = _load_runtime(
        config,
        enabled_reviewer,
        codex_backend,
        claude_model,
        claude_reasoning_effort,
        codex_model,
        codex_reasoning_effort,
        auto_post_review,
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
