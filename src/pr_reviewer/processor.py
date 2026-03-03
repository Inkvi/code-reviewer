from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pr_reviewer.config import AppConfig
from pr_reviewer.github import GitHubClient
from pr_reviewer.logger import info, warn
from pr_reviewer.models import PRCandidate, ProcessedState, ReviewerOutput
from pr_reviewer.output import write_review_markdown, write_reviewer_sidecar_markdown
from pr_reviewer.review_decision import infer_review_decision
from pr_reviewer.reviewers import (
    reconcile_reviews,
    run_claude_review,
    run_codex_review,
    run_codex_review_via_agents_sdk,
)
from pr_reviewer.state import StateStore
from pr_reviewer.workspace import PRWorkspace


def _disabled_output(reviewer: str) -> ReviewerOutput:
    now = datetime.now(UTC)
    return ReviewerOutput(
        reviewer=reviewer,
        status="disabled",
        markdown="",
        stdout="",
        stderr="",
        error="reviewer disabled by config",
        started_at=now,
        ended_at=now,
    )


def _single_reviewer_final_review(reviewer_output: ReviewerOutput) -> str:
    if reviewer_output.status == "ok" and reviewer_output.markdown.strip():
        return reviewer_output.markdown.strip()
    return (
        "### Findings\n"
        f"- Reviewer failed: {reviewer_output.error or 'unknown error'}.\n\n"
        "### Test Gaps\n"
        "- None noted."
    )


def _start_codex_review_task(config: AppConfig, pr: PRCandidate, workdir: Path) -> asyncio.Task:
    if config.codex_backend == "agents_sdk":
        return asyncio.create_task(
            run_codex_review_via_agents_sdk(
                pr,
                workdir,
                config.codex_timeout_seconds,
                config.codex_model,
                config.codex_reasoning_effort,
            )
        )
    return asyncio.create_task(
        run_codex_review(
            pr,
            workdir,
            config.codex_timeout_seconds,
            model=config.codex_model,
            reasoning_effort=config.codex_reasoning_effort,
        )
    )


def _existing_saved_review_path(
    output_root: Path,
    pr: PRCandidate,
    previous: ProcessedState,
) -> Path | None:
    candidates: list[Path] = []
    if previous.last_output_file:
        candidates.append(Path(previous.last_output_file))
    candidates.append(output_root / pr.owner / pr.repo / f"pr-{pr.number}.md")

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate
    return None


def _publish_and_persist(
    config: AppConfig,
    client: GitHubClient,
    store: StateStore,
    pr: PRCandidate,
    output_path: Path,
    review_text_for_decision: str,
    status_when_not_posted: str,
) -> None:
    posted_at = None
    status = status_when_not_posted
    if config.auto_submit_review_decision:
        decision = infer_review_decision(review_text_for_decision)
        info(f"{pr.key}: submitting PR review decision={decision}")
        client.submit_pr_review(pr, str(output_path), decision)
        posted_at = ProcessedState.now_iso()
        status = "approved" if decision == "approve" else "changes_requested"
        info(f"{pr.key}: submitted PR review ({status})")
    elif config.auto_post_review:
        info(f"{pr.key}: posting review comment to GitHub")
        client.post_pr_comment(pr, str(output_path))
        posted_at = ProcessedState.now_iso()
        status = "posted"
        info(f"Posted review comment for {pr.key}")
    else:
        info(
            f"{pr.key}: auto_post_review and auto_submit_review_decision are disabled; "
            "not posting to GitHub"
        )

    store.set(
        pr.key,
        ProcessedState(
            last_reviewed_head_sha=pr.head_sha,
            last_output_file=str(output_path.resolve()),
            last_status=status,
            last_posted_at=posted_at,
        ),
    )
    store.save()


async def process_candidate(
    config: AppConfig,
    client: GitHubClient,
    store: StateStore,
    workspace_mgr: PRWorkspace,
    pr: PRCandidate,
    *,
    use_saved_review: bool = False,
    ignore_saved_review: bool = False,
    ignore_existing_comment: bool = False,
    ignore_head_sha: bool = False,
) -> bool:
    info(f"Processing {pr.key}: {pr.title}")
    previous = store.get(pr.key)
    saved_review_path = _existing_saved_review_path(Path(config.output_dir), pr, previous)

    if use_saved_review and ignore_saved_review:
        raise ValueError("use_saved_review and ignore_saved_review cannot be enabled together")

    if use_saved_review:
        if saved_review_path is None:
            info(f"Skipping {pr.key}: use_saved_review requested but no saved review exists")
            previous.last_status = "skipped_missing_saved_review"
            store.set(pr.key, previous)
            store.save()
            return False
        info(f"{pr.key}: using saved review file ({saved_review_path})")

    if not use_saved_review:
        if ignore_saved_review:
            info(f"{pr.key}: force mode enabled, bypassing saved review dedupe")
        elif saved_review_path is not None:
            info(f"Skipping {pr.key}: saved review already exists ({saved_review_path})")
            previous.last_status = "skipped_existing_saved_review"
            store.set(pr.key, previous)
            store.save()
            return False

    if ignore_existing_comment:
        info(f"{pr.key}: force mode enabled, bypassing existing issue comment check")
    else:
        info(f"{pr.key}: checking existing issue comments")
        if client.has_issue_comment_by_viewer(pr):
            info(f"Skipping {pr.key}: viewer already commented on PR thread")
            previous.last_status = "skipped_existing_comment"
            store.set(pr.key, previous)
            store.save()
            return False

    info(f"{pr.key}: checking previous processed head SHA")
    if use_saved_review:
        info(f"{pr.key}: use_saved_review enabled, bypassing head SHA dedupe")
    elif ignore_head_sha:
        info(f"{pr.key}: force mode enabled, bypassing head SHA dedupe")
    elif previous.last_reviewed_head_sha and previous.last_reviewed_head_sha == pr.head_sha:
        info(f"Skipping {pr.key}: head SHA unchanged ({pr.head_sha[:12]})")
        return False

    if use_saved_review and saved_review_path is not None:
        review_text_for_decision = saved_review_path.read_text(encoding="utf-8")
        _publish_and_persist(
            config,
            client,
            store,
            pr,
            saved_review_path,
            review_text_for_decision,
            status_when_not_posted="reused_saved_review",
        )
        info(f"{pr.key}: processing complete (reused saved review)")
        return True

    workdir: Path | None = None
    try:
        info(f"{pr.key}: preparing workspace")
        workdir = workspace_mgr.prepare(pr)
        info(f"{pr.key}: workspace ready at {workdir}")

        enabled_reviewers = set(config.enabled_reviewers)
        pending_tasks: dict[str, asyncio.Task] = {}

        if "claude" in enabled_reviewers:
            info(
                f"{pr.key}: starting Claude review "
                f"(model={config.claude_model or 'default'}, "
                f"effort={config.claude_reasoning_effort or 'default'})"
            )
            pending_tasks["claude"] = asyncio.create_task(
                run_claude_review(
                    pr,
                    workdir,
                    config.claude_timeout_seconds,
                    model=config.claude_model,
                    reasoning_effort=config.claude_reasoning_effort,
                )
            )
        else:
            info(f"{pr.key}: Claude reviewer disabled")

        if "codex" in enabled_reviewers:
            info(
                f"{pr.key}: starting Codex review "
                f"(backend={config.codex_backend}, model={config.codex_model}, "
                f"effort={config.codex_reasoning_effort or 'default'})"
            )
            pending_tasks["codex"] = _start_codex_review_task(config, pr, workdir)
        else:
            info(f"{pr.key}: Codex reviewer disabled")

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
                    if reviewer_name == "codex" and output.stdout.startswith(
                        "codex JSON events captured:"
                    ):
                        info(f"{pr.key}: {output.stdout}")
                    if output.status != "ok" and output.error:
                        warn(f"{pr.key}: {reviewer_name} error: {output.error}")
                    pending_tasks.pop(reviewer_name)

        claude_output = reviewer_outputs.get("claude", _disabled_output("claude"))
        codex_output = reviewer_outputs.get("codex", _disabled_output("codex"))

        if enabled_reviewers == {"claude", "codex"}:
            info(f"{pr.key}: reconciling Claude and Codex outputs")
            final_review = await reconcile_reviews(
                pr,
                workdir,
                claude_output,
                codex_output,
                config.claude_timeout_seconds,
                claude_model=config.claude_model,
                claude_reasoning_effort=config.claude_reasoning_effort,
            )
        elif enabled_reviewers == {"claude"}:
            info(f"{pr.key}: single reviewer mode (claude)")
            final_review = _single_reviewer_final_review(claude_output)
        elif enabled_reviewers == {"codex"}:
            info(f"{pr.key}: single reviewer mode (codex)")
            final_review = _single_reviewer_final_review(codex_output)
        else:
            unsupported = sorted(enabled_reviewers)
            raise RuntimeError(f"Unsupported enabled_reviewers configuration: {unsupported}")
        info(f"{pr.key}: writing final markdown output")
        output_path = write_review_markdown(
            Path(config.output_dir),
            pr,
            final_review,
        )
        raw_output_path = write_reviewer_sidecar_markdown(
            Path(config.output_dir),
            pr,
            claude_output,
            codex_output,
            include_stderr=config.include_reviewer_stderr,
        )
        info(f"Final review ready: {output_path.resolve()}")
        info(f"Raw reviewer outputs: {raw_output_path.resolve()}")

        _publish_and_persist(
            config,
            client,
            store,
            pr,
            output_path,
            final_review,
            status_when_not_posted="generated",
        )
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
