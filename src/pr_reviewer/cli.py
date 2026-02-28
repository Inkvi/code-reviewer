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
PrUrlOption = Annotated[
    list[str],
    typer.Option(
        "--pr-url",
        help="GitHub pull request URL to review in force mode. Repeat for multiple PRs.",
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
    if codex_backend is None:
        return config
    payload = config.model_dump()
    payload["codex_backend"] = codex_backend
    try:
        return AppConfig.model_validate(payload)
    except ValidationError as exc:
        raise typer.BadParameter(
            f"Invalid --codex-backend value: {exc.errors(include_url=False)}"
        ) from exc


def _load_runtime(
    config_path: Path,
    enabled_reviewer: list[str] | None,
    codex_backend: str | None,
) -> tuple[AppConfig, StateStore]:
    config = load_config(config_path)
    config = _apply_enabled_reviewer_override(config, enabled_reviewer)
    config = _apply_codex_backend_override(config, codex_backend)
    store = StateStore(Path(config.state_file))
    store.acquire_lock()
    store.load()
    return config, store


@app.command("check")
def check_command(
    config: ConfigOption = Path("config.toml"),
    enabled_reviewer: EnabledReviewerOption = None,
    codex_backend: CodexBackendOption = None,
) -> None:
    """Run preflight checks and print runtime summary."""
    cfg = load_config(config)
    cfg = _apply_enabled_reviewer_override(cfg, enabled_reviewer)
    cfg = _apply_codex_backend_override(cfg, codex_backend)
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
    table.add_row("Codex backend", cfg.codex_backend)
    table.add_row("Codex model", cfg.codex_model)
    table.add_row("Output dir", str(Path(cfg.output_dir).resolve()))
    table.add_row("State file", str(Path(cfg.state_file).resolve()))
    console.print(table)


@app.command("run-once")
def run_once_command(
    config: ConfigOption = Path("config.toml"),
    enabled_reviewer: EnabledReviewerOption = None,
    codex_backend: CodexBackendOption = None,
) -> None:
    """Run one polling cycle."""
    cfg, store = _load_runtime(config, enabled_reviewer, codex_backend)
    try:
        preflight = run_preflight(cfg)
        processed = asyncio.run(run_cycle(cfg, preflight, store))
        info(f"run-once finished. Processed {processed} PR(s)")
    finally:
        store.release_lock()


@app.command("start")
def start_command(
    config: ConfigOption = Path("config.toml"),
    enabled_reviewer: EnabledReviewerOption = None,
    codex_backend: CodexBackendOption = None,
) -> None:
    """Run daemon forever."""
    cfg, store = _load_runtime(config, enabled_reviewer, codex_backend)
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


@app.command("force")
def force_command(
    pr_url: PrUrlOption,
    config: ConfigOption = Path("config.toml"),
    enabled_reviewer: EnabledReviewerOption = None,
    codex_backend: CodexBackendOption = None,
) -> None:
    """Force review specific PR URL(s), bypassing reviewer-assignment and skip checks."""
    if not pr_url:
        raise typer.BadParameter("Provide at least one --pr-url value.")

    cfg, store = _load_runtime(config, enabled_reviewer, codex_backend)
    try:
        preflight = run_preflight(cfg)
        client = GitHubClient(viewer_login=preflight.viewer_login)
        workspace_mgr = PRWorkspace(Path(cfg.clone_root))

        deduped_urls = list(dict.fromkeys(pr_url))

        async def _run_force() -> int:
            processed = 0
            for index, url in enumerate(deduped_urls, start=1):
                info(f"Force PR {index}/{len(deduped_urls)}: {url}")
                candidate = client.get_pr_candidate(url)
                changed = await process_candidate(
                    cfg,
                    client,
                    store,
                    workspace_mgr,
                    candidate,
                    ignore_existing_comment=True,
                    ignore_head_sha=True,
                )
                if changed:
                    processed += 1
            return processed

        processed = asyncio.run(_run_force())
        info(f"force finished. Processed {processed} PR(s)")
    finally:
        store.release_lock()
