from __future__ import annotations

import asyncio
from pathlib import Path

from pr_reviewer.config import AppConfig
from pr_reviewer.github import GitHubClient
from pr_reviewer.logger import info, warn
from pr_reviewer.models import PRCandidate, ProcessedState, ReviewerOutput
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
    info(f"Processing {pr.key}: {pr.title}")
    info(f"{pr.key}: checking existing issue comments")
    if client.has_issue_comment_by_viewer(pr):
        info(f"Skipping {pr.key}: viewer already commented on PR thread")
        state = store.get(pr.key)
        state.last_status = "skipped_existing_comment"
        store.set(pr.key, state)
        store.save()
        return False

    info(f"{pr.key}: checking previous processed head SHA")
    previous = store.get(pr.key)
    if previous.last_reviewed_head_sha and previous.last_reviewed_head_sha == pr.head_sha:
        info(f"Skipping {pr.key}: head SHA unchanged ({pr.head_sha[:12]})")
        return False

    workdir: Path | None = None
    try:
        info(f"{pr.key}: preparing workspace")
        workdir = workspace_mgr.prepare(pr)
        info(f"{pr.key}: workspace ready at {workdir}")

        info(f"{pr.key}: starting Claude review")
        claude_task = asyncio.create_task(
            run_claude_review(pr, workdir, config.claude_timeout_seconds)
        )
        info(f"{pr.key}: starting Codex review")
        codex_task = asyncio.create_task(
            run_codex_review(pr, workdir, config.codex_timeout_seconds)
        )

        pending_tasks: dict[str, asyncio.Task] = {
            "claude": claude_task,
            "codex": codex_task,
        }
        reviewer_outputs: dict[str, ReviewerOutput] = {}

        while pending_tasks:
            done, _ = await asyncio.wait(
                pending_tasks.values(),
                timeout=20,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                running = ", ".join(pending_tasks.keys())
                info(f"{pr.key}: reviewers still running ({running})")
                continue

            for reviewer_name, task in list(pending_tasks.items()):
                if task in done:
                    output = await task
                    reviewer_outputs[reviewer_name] = output
                    info(
                        f"{pr.key}: {reviewer_name} finished "
                        f"status={output.status} duration={output.duration_seconds:.1f}s"
                    )
                    if output.status != "ok" and output.error:
                        warn(f"{pr.key}: {reviewer_name} error: {output.error}")
                    pending_tasks.pop(reviewer_name)

        claude_output = reviewer_outputs["claude"]
        codex_output = reviewer_outputs["codex"]

        info(f"{pr.key}: reconciling Claude and Codex outputs")
        final_review = await reconcile_reviews(
            pr,
            workdir,
            claude_output,
            codex_output,
            config.claude_timeout_seconds,
        )
        info(f"{pr.key}: writing final markdown output")
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
            info(f"{pr.key}: posting review comment to GitHub")
            client.post_pr_comment(pr, str(output_path))
            posted_at = ProcessedState.now_iso()
            info(f"Posted review comment for {pr.key}")
        else:
            info(f"{pr.key}: auto_post_review disabled, not posting comment")

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
        info(f"{pr.key}: processing complete")
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
