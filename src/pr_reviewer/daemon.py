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


async def run_cycle(config: AppConfig, preflight: PreflightResult, store: StateStore) -> int:
    client = GitHubClient(viewer_login=preflight.viewer_login)
    workspace_mgr = PRWorkspace(Path(config.clone_root))

    processed = 0
    try:
        candidates = client.discover_pr_candidates(config)
    except Exception as exc:  # noqa: BLE001
        warn(f"Failed to discover PRs: {exc}")
        return 0

    if not candidates:
        info("No candidate PRs found")
        return 0

    info(f"Found {len(candidates)} candidate PR(s)")

    if config.max_parallel_prs == 1:
        for index, pr in enumerate(candidates, start=1):
            info(f"PR {index}/{len(candidates)}: {pr.key}")
            changed = await process_candidate(config, client, store, workspace_mgr, pr)
            if changed:
                processed += 1
        return processed

    semaphore = asyncio.Semaphore(config.max_parallel_prs)

    async def _bounded_process(pr: PRCandidate) -> bool:
        async with semaphore:
            return await process_candidate(config, client, store, workspace_mgr, pr)

    tasks = [asyncio.create_task(_bounded_process(pr)) for pr in candidates]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            warn(f"Parallel PR processing failed: {result}")
        elif result:
            processed += 1

    return processed


async def start_daemon(config: AppConfig, preflight: PreflightResult, store: StateStore) -> None:
    info(f"Starting daemon with interval={config.poll_interval_seconds}s org={config.github_org}")
    while True:
        try:
            processed = await run_cycle(config, preflight, store)
            info(f"Cycle complete. Processed {processed} PR(s)")
        except Exception as exc:  # noqa: BLE001
            warn(f"Cycle failed: {exc}")
        await asyncio.sleep(config.poll_interval_seconds)
