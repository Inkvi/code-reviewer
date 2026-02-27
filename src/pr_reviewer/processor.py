from __future__ import annotations

import asyncio
from pathlib import Path

from pr_reviewer.config import AppConfig
from pr_reviewer.github import GitHubClient
from pr_reviewer.logger import info, warn
from pr_reviewer.models import PRCandidate, ProcessedState
from pr_reviewer.output import write_review_markdown
from pr_reviewer.reviewers import reconcile_reviews, run_claude_review, run_codex_review
from pr_reviewer.state import StateStore
from pr_reviewer.workspace import PRWorkspace


async def process_candidate(
    config: AppConfig,
    client: GitHubClient,
    store: StateStore,
    workspace_mgr: PRWorkspace,
    pr: PRCandidate,
) -> bool:
    if client.has_issue_comment_by_viewer(pr):
        info(f"Skipping {pr.key}: viewer already commented on PR thread")
        state = store.get(pr.key)
        state.last_status = "skipped_existing_comment"
        store.set(pr.key, state)
        store.save()
        return False

    previous = store.get(pr.key)
    if previous.last_reviewed_head_sha and previous.last_reviewed_head_sha == pr.head_sha:
        info(f"Skipping {pr.key}: head SHA unchanged ({pr.head_sha[:12]})")
        return False

    workdir: Path | None = None
    try:
        workdir = workspace_mgr.prepare(pr)
        claude_task = asyncio.create_task(
            run_claude_review(pr, workdir, config.claude_timeout_seconds)
        )
        codex_task = asyncio.create_task(
            run_codex_review(pr, workdir, config.codex_timeout_seconds)
        )

        claude_output, codex_output = await asyncio.gather(claude_task, codex_task)

        final_review = await reconcile_reviews(
            pr,
            workdir,
            claude_output,
            codex_output,
            config.claude_timeout_seconds,
        )
        output_path = write_review_markdown(
            Path(config.output_dir),
            pr,
            final_review,
            claude_output,
            codex_output,
        )
        info(f"Final review ready: {output_path.resolve()}")

        posted_at = None
        if config.auto_post_review:
            client.post_pr_comment(pr, str(output_path))
            posted_at = ProcessedState.now_iso()
            info(f"Posted review comment for {pr.key}")

        store.set(
            pr.key,
            ProcessedState(
                last_reviewed_head_sha=pr.head_sha,
                last_output_file=str(output_path.resolve()),
                last_status="posted" if posted_at else "generated",
                last_posted_at=posted_at,
            ),
        )
        store.save()
        return True
    except Exception as exc:  # noqa: BLE001
        warn(f"Failed processing {pr.key}: {exc}")
        state = store.get(pr.key)
        state.last_status = f"error: {exc}"
        store.set(pr.key, state)
        store.save()
        return False
    finally:
        if workdir is not None:
            workspace_mgr.cleanup(workdir)
