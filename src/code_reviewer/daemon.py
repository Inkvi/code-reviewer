from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from pathlib import Path

from code_reviewer.config import AppConfig
from code_reviewer.github import GitHubClient
from code_reviewer.github_app_auth import is_github_app_auth, refresh_github_token
from code_reviewer.logger import info, warn
from code_reviewer.models import PRCandidate
from code_reviewer.preflight import PreflightResult
from code_reviewer.processor import process_candidate
from code_reviewer.state import StateStore
from code_reviewer.workspace import PRWorkspace


async def run_cycle(
    config: AppConfig,
    preflight: PreflightResult,
    store: StateStore,
    *,
    verbose: bool = True,
) -> int:
    client = GitHubClient(viewer_login=preflight.viewer_login)
    workspace_mgr = PRWorkspace(Path(config.clone_root))

    processed = 0
    try:
        candidates = client.discover_pr_candidates(config)
    except Exception as exc:  # noqa: BLE001
        warn(f"Failed to discover PRs: {exc}")
        return 0

    if config.slash_command_enabled:
        try:
            slash_candidates = client.discover_slash_command_candidates(config, store)
        except Exception as exc:  # noqa: BLE001
            warn(f"Failed to discover slash command PRs: {exc}")
            slash_candidates = []

        existing_keys = {pr.key.lower() for pr in candidates}
        for sc in slash_candidates:
            if sc.key.lower() not in existing_keys:
                candidates.append(sc)
            else:
                candidates = [sc if c.key.lower() == sc.key.lower() else c for c in candidates]

    if not candidates:
        if verbose:
            info("No candidate PRs found")
        return 0

    if verbose:
        info(f"Found {len(candidates)} candidate PR(s)")

    if config.max_parallel_prs == 1:
        for index, pr in enumerate(candidates, start=1):
            if verbose:
                info(f"PR {index}/{len(candidates)} {pr.url}")
            result = await process_candidate(
                config,
                client,
                store,
                workspace_mgr,
                pr,
                verbose=verbose,
            )
            if result.processed:
                processed += 1
        return processed

    semaphore = asyncio.Semaphore(config.max_parallel_prs)

    async def _bounded_process(pr: PRCandidate) -> bool:
        async with semaphore:
            r = await process_candidate(
                config,
                client,
                store,
                workspace_mgr,
                pr,
                verbose=verbose,
            )
            return r.processed

    tasks = [asyncio.create_task(_bounded_process(pr)) for pr in candidates]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            warn(f"Parallel PR processing failed: {result}")
        elif result:
            processed += 1

    return processed


async def start_daemon(
    config: AppConfig,
    preflight: PreflightResult,
    store: StateStore,
    *,
    reload_config: Callable[[], AppConfig] | None = None,
) -> None:
    info(
        "Starting daemon with "
        f"interval={config.poll_interval_seconds}s owners={','.join(config.github_owners)}"
    )
    if is_github_app_auth():
        info("GitHub App auth detected — tokens will refresh each cycle")

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    while not shutdown.is_set():
        if reload_config is not None:
            try:
                config = reload_config()
            except Exception as exc:  # noqa: BLE001
                warn(f"Config reload failed, using previous config: {exc}")
        try:
            refresh_github_token()
            processed = await run_cycle(config, preflight, store, verbose=False)
            info(f"Cycle complete. Processed {processed} PR(s)")
        except Exception as exc:  # noqa: BLE001
            warn(f"Cycle failed: {exc}")
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=config.poll_interval_seconds)
        except TimeoutError:
            pass

    info("Shutting down daemon")
