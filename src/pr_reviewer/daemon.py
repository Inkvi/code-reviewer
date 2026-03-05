from __future__ import annotations

import asyncio
from pathlib import Path

from pr_reviewer.config import AppConfig
from pr_reviewer.github import GitHubClient
from pr_reviewer.logger import info, warn
from pr_reviewer.models import PRCandidate
from pr_reviewer.preflight import PreflightResult
from pr_reviewer.processor import process_candidate
from pr_reviewer.state import StateStore
from pr_reviewer.workspace import PRWorkspace


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
                candidates = [
                    sc if c.key.lower() == sc.key.lower() else c for c in candidates
                ]

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


async def start_daemon(config: AppConfig, preflight: PreflightResult, store: StateStore) -> None:
    info(
        "Starting daemon with "
        f"interval={config.poll_interval_seconds}s owners={','.join(config.github_owners)}"
    )
    while True:
        try:
            processed = await run_cycle(config, preflight, store, verbose=False)
            info(f"Cycle complete. Processed {processed} PR(s)")
        except Exception as exc:  # noqa: BLE001
            warn(f"Cycle failed: {exc}")
        await asyncio.sleep(config.poll_interval_seconds)
