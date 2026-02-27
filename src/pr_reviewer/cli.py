from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from pr_reviewer.config import AppConfig, load_config
from pr_reviewer.daemon import run_cycle, start_daemon
from pr_reviewer.logger import console, error, info
from pr_reviewer.preflight import run_preflight
from pr_reviewer.state import StateStore

app = typer.Typer(add_completion=False, help="PR review daemon")
ConfigOption = Annotated[Path, typer.Option(..., exists=True, dir_okay=False)]


def _load_runtime(config_path: Path) -> tuple[AppConfig, StateStore]:
    config = load_config(config_path)
    store = StateStore(Path(config.state_file))
    store.acquire_lock()
    store.load()
    return config, store


@app.command("check")
def check_command(config: ConfigOption) -> None:
    """Run preflight checks and print runtime summary."""
    cfg = load_config(config)
    preflight = run_preflight()

    table = Table(title="pr-reviewer check")
    table.add_column("Item")
    table.add_column("Value")
    table.add_row("GitHub org", cfg.github_org)
    table.add_row("Viewer", preflight.viewer_login)
    table.add_row("Poll interval", str(cfg.poll_interval_seconds))
    table.add_row("Auto post", str(cfg.auto_post_review))
    table.add_row("Output dir", str(Path(cfg.output_dir).resolve()))
    table.add_row("State file", str(Path(cfg.state_file).resolve()))
    console.print(table)


@app.command("run-once")
def run_once_command(config: ConfigOption) -> None:
    """Run one polling cycle."""
    cfg, store = _load_runtime(config)
    try:
        preflight = run_preflight()
        processed = asyncio.run(run_cycle(cfg, preflight, store))
        info(f"run-once finished. Processed {processed} PR(s)")
    finally:
        store.release_lock()


@app.command("start")
def start_command(config: ConfigOption) -> None:
    """Run daemon forever."""
    cfg, store = _load_runtime(config)
    try:
        preflight = run_preflight()
        asyncio.run(start_daemon(cfg, preflight, store))
    except KeyboardInterrupt:
        info("Shutting down daemon")
    except Exception as exc:  # noqa: BLE001
        error(str(exc))
        raise typer.Exit(code=1) from exc
    finally:
        store.release_lock()
