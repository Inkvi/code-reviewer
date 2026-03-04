from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
    run_gemini_review,
)
from pr_reviewer.state import StateStore
from pr_reviewer.workspace import PRWorkspace

DecisionReason = Literal[
    "bootstrap_missing_state",
    "new_rerequest",
    "no_new_trigger",
    "missing_rerequest_data",
]


@dataclass(slots=True)
class ProcessingDecision:
    should_process: bool
    reason: DecisionReason
    next_expected_rerequest_at: str | None = None


_CONFIG_LIKE_SUFFIXES = {
    ".cfg",
    ".conf",
    ".env",
    ".ini",
    ".json",
    ".properties",
    ".toml",
    ".yaml",
    ".yml",
}


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


def _resolve_reconciler_settings(config: AppConfig) -> tuple[str, int, str | None, str | None]:
    backend = config.reconciler_backend
    if backend == "claude":
        model = config.reconciler_model or config.claude_model
        reasoning_effort = config.reconciler_reasoning_effort or config.claude_reasoning_effort
        timeout_seconds = config.claude_timeout_seconds
    elif backend == "codex":
        model = config.reconciler_model or config.codex_model
        reasoning_effort = config.reconciler_reasoning_effort or config.codex_reasoning_effort
        timeout_seconds = config.codex_timeout_seconds
    else:
        model = config.reconciler_model or config.gemini_model
        reasoning_effort = None
        timeout_seconds = config.gemini_timeout_seconds
    return backend, timeout_seconds, model, reasoning_effort


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


def _is_config_like_path(path: str) -> bool:
    normalized = path.strip().lower()
    if not normalized:
        return False
    file_name = Path(normalized).name
    if file_name in {"docker-compose", "docker-compose.yaml", "docker-compose.yml"}:
        return True
    return Path(normalized).suffix in _CONFIG_LIKE_SUFFIXES


def _skip_reason_for_change_scope(pr: PRCandidate) -> str | None:
    total_lines_changed = pr.additions + pr.deletions
    if total_lines_changed < 10:
        return "small_change_set"

    if pr.changed_file_paths and all(_is_config_like_path(path) for path in pr.changed_file_paths):
        return "config_only_files"

    return None


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _output_version_label(pr: PRCandidate, *, now: datetime | None = None) -> str:
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    short_sha = pr.head_sha[:12] if pr.head_sha else "nohead"
    return f"{timestamp}-{short_sha}"


def _compute_processing_decision(
    previous: ProcessedState,
    pr: PRCandidate,
    trigger_mode: str,
) -> ProcessingDecision:
    if previous.last_processed_at is None:
        return ProcessingDecision(
            should_process=True,
            reason="bootstrap_missing_state",
            next_expected_rerequest_at=pr.latest_direct_rerequest_at,
        )

    latest_direct_rerequest_at = _parse_iso_timestamp(pr.latest_direct_rerequest_at)
    if latest_direct_rerequest_at is None:
        return ProcessingDecision(
            should_process=False,
            reason="missing_rerequest_data",
            next_expected_rerequest_at=previous.last_seen_rerequest_at,
        )

    last_seen_rerequest_at = _parse_iso_timestamp(previous.last_seen_rerequest_at)
    if last_seen_rerequest_at is None or latest_direct_rerequest_at > last_seen_rerequest_at:
        return ProcessingDecision(
            should_process=True,
            reason="new_rerequest",
            next_expected_rerequest_at=pr.latest_direct_rerequest_at,
        )

    if trigger_mode == "rerequest_or_commit":
        # Commit-trigger processing is reserved for future implementation.
        pass

    return ProcessingDecision(
        should_process=False,
        reason="no_new_trigger",
        next_expected_rerequest_at=previous.last_seen_rerequest_at,
    )


def _publish_and_persist(
    config: AppConfig,
    client: GitHubClient,
    store: StateStore,
    pr: PRCandidate,
    output_path: Path,
    review_text_for_decision: str,
    status_when_not_posted: str,
    previous: ProcessedState,
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

    last_seen_rerequest_at = previous.last_seen_rerequest_at
    if pr.latest_direct_rerequest_at is not None:
        last_seen_rerequest_at = pr.latest_direct_rerequest_at

    store.set(
        pr.key,
        ProcessedState(
            last_reviewed_head_sha=pr.head_sha,
            last_processed_at=ProcessedState.now_iso(),
            last_seen_rerequest_at=last_seen_rerequest_at,
            trigger_mode=config.trigger_mode,
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
    verbose: bool = True,
) -> bool:
    def detail(message: str) -> None:
        if verbose:
            info(message)

    detail(f"Processing {pr.key}: {pr.title}")
    previous = store.get(pr.key)
    skip_reason = _skip_reason_for_change_scope(pr)
    if skip_reason is not None:
        detail(f"Skipping {pr.key}: {skip_reason}")
        previous.last_status = f"skipped_{skip_reason}"
        previous.trigger_mode = config.trigger_mode
        store.set(pr.key, previous)
        store.save()
        return False

    if use_saved_review:
        saved_review_path = _existing_saved_review_path(Path(config.output_dir), pr, previous)
        if saved_review_path is None:
            detail(f"Skipping {pr.key}: use_saved_review requested but no saved review exists")
            previous.last_status = "skipped_missing_saved_review"
            previous.trigger_mode = config.trigger_mode
            store.set(pr.key, previous)
            store.save()
            return False
        detail(f"{pr.key}: using saved review file ({saved_review_path})")
        review_text_for_decision = saved_review_path.read_text(encoding="utf-8")
        _publish_and_persist(
            config,
            client,
            store,
            pr,
            saved_review_path,
            review_text_for_decision,
            status_when_not_posted="reused_saved_review",
            previous=previous,
        )
        info(f"{pr.key}: processing complete (reused saved review)")
        return True

    decision = _compute_processing_decision(previous, pr, config.trigger_mode)
    if decision.should_process:
        detail(f"{pr.key}: trigger check passed ({decision.reason})")
    else:
        detail(f"Skipping {pr.key}: trigger check skipped ({decision.reason})")
        previous.last_status = f"skipped_{decision.reason}"
        previous.trigger_mode = config.trigger_mode
        store.set(pr.key, previous)
        store.save()
        return False

    workdir: Path | None = None
    try:
        info(f"{pr.key}: preparing workspace")
        workdir = workspace_mgr.prepare(pr)
        info(f"{pr.key}: workspace ready at {workdir}")

        enabled_reviewers = list(config.enabled_reviewers)
        enabled_reviewer_set = set(enabled_reviewers)
        pending_tasks: dict[str, asyncio.Task] = {}

        if "claude" in enabled_reviewer_set:
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

        if "codex" in enabled_reviewer_set:
            info(
                f"{pr.key}: starting Codex review "
                f"(backend={config.codex_backend}, model={config.codex_model}, "
                f"effort={config.codex_reasoning_effort or 'default'})"
            )
            pending_tasks["codex"] = _start_codex_review_task(config, pr, workdir)
        else:
            info(f"{pr.key}: Codex reviewer disabled")

        if "gemini" in enabled_reviewer_set:
            info(
                f"{pr.key}: starting Gemini review "
                f"(model={config.gemini_model or 'default'})"
            )
            pending_tasks["gemini"] = asyncio.create_task(
                run_gemini_review(
                    pr,
                    workdir,
                    config.gemini_timeout_seconds,
                    model=config.gemini_model,
                )
            )
        else:
            info(f"{pr.key}: Gemini reviewer disabled")

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

        active_outputs = {
            name: reviewer_outputs.get(name, _disabled_output(name))
            for name in enabled_reviewers
        }

        if len(enabled_reviewers) >= 2:
            reviewer_names = " + ".join(enabled_reviewers)
            (
                reconciler_backend,
                reconciler_timeout_seconds,
                reconciler_model,
                reconciler_reasoning_effort,
            ) = _resolve_reconciler_settings(config)
            effort_label = (
                reconciler_reasoning_effort or "default"
                if reconciler_backend != "gemini"
                else "n/a"
            )
            info(
                f"{pr.key}: reconciling {reviewer_names} outputs "
                f"(backend={reconciler_backend}, model={reconciler_model or 'default'}, "
                f"effort={effort_label})"
            )
            pr_comments: list[str] = []
            try:
                pr_comments = client.get_pr_issue_comments(pr)
            except Exception as exc:  # noqa: BLE001
                warn(f"{pr.key}: failed to fetch PR issue comments for reconciliation: {exc}")
            final_review = await reconcile_reviews(
                pr,
                workdir,
                list(active_outputs.values()),
                reconciler_timeout_seconds,
                reconciler_backend=reconciler_backend,
                pr_comments=pr_comments,
                reconciler_model=reconciler_model,
                reconciler_reasoning_effort=reconciler_reasoning_effort,
            )
        elif len(enabled_reviewers) == 1:
            sole_reviewer = enabled_reviewers[0]
            info(f"{pr.key}: single reviewer mode ({sole_reviewer})")
            final_review = _single_reviewer_final_review(active_outputs[sole_reviewer])
        else:
            raise RuntimeError("No enabled reviewers configured")
        info(f"{pr.key}: writing final markdown output")
        version_label = _output_version_label(pr)
        output_path = write_review_markdown(
            Path(config.output_dir),
            pr,
            final_review,
            version_label=version_label,
        )
        raw_output_path = write_reviewer_sidecar_markdown(
            Path(config.output_dir),
            pr,
            active_outputs,
            include_stderr=config.include_reviewer_stderr,
            version_label=version_label,
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
            previous=previous,
        )
        info(f"{pr.key}: processing complete")
        return True
    except Exception as exc:  # noqa: BLE001
        warn(f"Failed processing {pr.key}: {exc}")
        state = store.get(pr.key)
        state.last_status = f"error: {exc}"
        state.trigger_mode = config.trigger_mode
        store.set(pr.key, state)
        store.save()
        return False
    finally:
        if workdir is not None:
            workspace_mgr.cleanup(workdir)
