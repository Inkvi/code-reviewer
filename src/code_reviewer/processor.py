from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from code_reviewer.backend_usage import (
    BackendUsageSnapshot,
    decide_backend_usage,
    load_backend_usage_snapshot,
)
from code_reviewer.config import AppConfig
from code_reviewer.github import GitHubClient
from code_reviewer.logger import info, warn
from code_reviewer.models import (
    PRCandidate,
    ProcessedState,
    ProcessingResult,
    ReviewerOutput,
    ReviewerOutputSummary,
    TokenUsage,
)
from code_reviewer.output import (
    write_conversation_jsonl,
    write_review_markdown,
    write_review_meta,
    write_stage_markdown,
)
from code_reviewer.progress import ProgressComment
from code_reviewer.prompts import PromptBundle, PromptOverrideError, format_prompt_bundle
from code_reviewer.repos import fetch_remote_skills
from code_reviewer.review_decision import ReviewDecision, infer_review_decision
from code_reviewer.reviewers import (
    TriageResult,
    reconcile_reviews,
    run_claude_cli_review,
    run_claude_review,
    run_codex_review,
    run_codex_review_via_agents_sdk,
    run_gemini_review,
    run_lightweight_review,
    run_triage,
)
from code_reviewer.reviewers._circuit_breaker import is_open as _circuit_is_open
from code_reviewer.reviewers._circuit_breaker import record_failure as _circuit_record_failure
from code_reviewer.reviewers._circuit_breaker import record_success as _circuit_record_success
from code_reviewer.shell import CommandError
from code_reviewer.skills import inject_skill_paths, inject_skills, remove_injected_skills
from code_reviewer.state import StateStore
from code_reviewer.workspace import PRWorkspace

DecisionReason = Literal[
    "bootstrap_missing_state",
    "new_rerequest",
    "no_new_trigger",
    "missing_rerequest_data",
]

_MINIMUM_BACKEND_USAGE_PERCENT = 10.0


@dataclass(slots=True)
class ProcessingDecision:
    should_process: bool
    reason: DecisionReason
    next_expected_rerequest_at: str | None = None


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


def _extract_injection_section(text: str) -> tuple[str, str | None]:
    """Extract and strip all '### Prompt Injection Detection' sections from review output.

    Returns (cleaned_text, combined_injection_detail_or_None).
    """
    # Match section header with optional body; handles EOF with or without trailing newline
    pattern = r"### Prompt Injection Detection[^\S\n]*\n?([\s\S]*?)(?=\n###\s|\Z)"
    matches = list(re.finditer(pattern, text))
    if not matches:
        return text, None
    details: list[str] = []
    for m in matches:
        content = m.group(1).strip()
        # "None detected." means no injection found — skip it
        if content.lower() in ("none detected.", "none detected"):
            continue
        if content:
            details.append(content)
    cleaned = re.sub(pattern, "", text).strip()
    combined = "\n".join(details) if details else None
    return cleaned, combined


def _validate_review_format(
    text: str, *, pr_url: str = "", injection_protection: bool = True
) -> str:
    if injection_protection:
        from rich.markup import escape as rich_escape

        cleaned, injection_detail = _extract_injection_section(text)
        if injection_detail:
            warn(f"prompt injection detected in review output{' ' + pr_url if pr_url else ''}:")
            for line in injection_detail.splitlines():
                warn(f"  {rich_escape(line)}")
    else:
        cleaned = text
    if "### Findings" not in cleaned or "### Test Gaps" not in cleaned:
        return (
            "### Findings\n"
            "- [P0] Review output failed format validation.\n\n"
            "### Test Gaps\n"
            "- None noted."
        )
    return cleaned


def _single_reviewer_final_review(reviewer_output: ReviewerOutput) -> str:
    if reviewer_output.status == "ok" and reviewer_output.markdown.strip():
        return reviewer_output.markdown.strip()
    return (
        "### Findings\n"
        f"- Reviewer failed: {reviewer_output.error or 'unknown error'}.\n\n"
        "### Test Gaps\n"
        "- None noted."
    )


def _successful_outputs(
    active_outputs: dict[str, ReviewerOutput],
) -> dict[str, ReviewerOutput]:
    """Return only reviewer outputs with status 'ok'."""
    return {name: out for name, out in active_outputs.items() if out.status == "ok"}


def _all_failed_review() -> str:
    return (
        "### Findings\n"
        "- All reviewer backends failed. No review could be produced.\n\n"
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
                config.full_review_prompt_path,
            )
        )
    return asyncio.create_task(
        run_codex_review(
            pr,
            workdir,
            config.codex_timeout_seconds,
            model=config.codex_model,
            reasoning_effort=config.codex_reasoning_effort,
            prompt_path=config.full_review_prompt_path,
        )
    )


def _start_claude_review_task(config: AppConfig, pr: PRCandidate, workdir: Path) -> asyncio.Task:
    if config.claude_backend == "cli":
        return asyncio.create_task(
            run_claude_cli_review(
                pr,
                workdir,
                config.claude_timeout_seconds,
                model=config.claude_model,
                reasoning_effort=config.claude_reasoning_effort,
                prompt_path=config.full_review_prompt_path,
            )
        )
    return asyncio.create_task(
        run_claude_review(
            pr,
            workdir,
            config.claude_timeout_seconds,
            model=config.claude_model,
            reasoning_effort=config.claude_reasoning_effort,
            prompt_path=config.full_review_prompt_path,
        )
    )


def _usage_snapshot_for_model(
    snapshot: BackendUsageSnapshot,
    model: str | None,
) -> BackendUsageSnapshot | None:
    if snapshot.backend != "gemini" or not model:
        return snapshot
    window = snapshot.latest_by_limit.get(model)
    if window is None:
        return None
    return BackendUsageSnapshot(
        backend=snapshot.backend,
        events_scanned=snapshot.events_scanned,
        latest_by_limit={model: window},
        account_type=snapshot.account_type,
    )


def _backend_has_available_usage(
    backend: str,
    model: str | None,
) -> tuple[bool, str | None]:
    backend_name = backend.capitalize()
    try:
        snapshot = load_backend_usage_snapshot(backend)
    except Exception as exc:  # noqa: BLE001
        info(f"{backend_name} usage check unavailable ({exc})")
        return True, None

    if not snapshot.latest_by_limit:
        return True, None

    relevant_snapshot = _usage_snapshot_for_model(snapshot, model)
    if relevant_snapshot is None:
        info(
            f"{backend_name} usage check unavailable for configured model "
            f"{model}; proceeding without quota gating"
        )
        return True, None

    decision = decide_backend_usage(
        relevant_snapshot,
        minimum_remaining_percent=_MINIMUM_BACKEND_USAGE_PERCENT,
    )
    if not decision.should_use_backend:
        return False, decision.reason
    return True, None


def _resolve_gemini_review_model(
    config: AppConfig,
    context: str,
) -> tuple[bool, str | None]:
    """Pick the best available Gemini model, trying fallback if primary is blocked.

    Returns (available, model). available=False means all models are blocked.
    model may be None when using the CLI default.
    """
    candidates = [("primary", config.gemini_model)]
    if config.gemini_fallback_model and config.gemini_fallback_model != config.gemini_model:
        candidates.append(("fallback", config.gemini_fallback_model))

    for label, model in candidates:
        opened, reason = _circuit_is_open("gemini", model)
        if opened:
            warn(f"Gemini {label} model {model or 'default'} circuit open: {reason} {context}")
            continue
        allowed, usage_reason = _backend_has_available_usage("gemini", model)
        if not allowed:
            warn(f"Gemini {label} model {model or 'default'} usage gate: {usage_reason} {context}")
            continue
        if label == "fallback":
            info(f"using Gemini fallback model {model} {context}")
        return True, model
    return False, None


def _resolve_reconciler_settings(
    config: AppConfig,
) -> tuple[list[str], dict[str, int], str | None, str | None]:
    backends = config.reconciler_backend
    primary = backends[0]
    if primary == "claude":
        model = config.reconciler_model or config.claude_model
        reasoning_effort = config.reconciler_reasoning_effort or config.claude_reasoning_effort
    elif primary == "codex":
        model = config.reconciler_model or config.codex_model
        reasoning_effort = config.reconciler_reasoning_effort or config.codex_reasoning_effort
    else:
        model = config.reconciler_model or config.gemini_model
        reasoning_effort = None
    backend_timeouts: dict[str, int] = {}
    for b in backends:
        if b == "claude":
            backend_timeouts[b] = config.claude_timeout_seconds
        elif b == "codex":
            backend_timeouts[b] = config.codex_timeout_seconds
        else:
            backend_timeouts[b] = config.gemini_timeout_seconds
    return backends, backend_timeouts, model, reasoning_effort


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


def _build_review_meta(
    pr: PRCandidate,
    config: AppConfig,
    review_type: str,
    *,
    triage_result: str | None = None,
    reviewer_outputs: dict[str, ReviewerOutput] | None = None,
    lightweight_usage: TokenUsage | None = None,
    started_at: datetime | None = None,
) -> dict:
    """Build metadata dict for a review artifact."""
    meta: dict = {
        "author": pr.author_login,
        "review_type": review_type,
    }
    if pr.title:
        meta["title"] = pr.title
    if not pr.is_local and pr.url:
        meta["url"] = pr.url
    meta["base_ref"] = pr.base_ref
    meta["head_sha"] = pr.head_sha
    meta["additions"] = pr.additions
    meta["deletions"] = pr.deletions
    if pr.changed_file_paths:
        meta["changed_files"] = pr.changed_file_paths
    if triage_result:
        meta["triage_result"] = triage_result

    # Trigger info
    if pr.slash_command_trigger:
        meta["trigger"] = {
            "type": "slash_command",
            "by": pr.slash_command_trigger.comment_author,
            "at": pr.slash_command_trigger.comment_created_at,
            "force": pr.slash_command_trigger.force,
        }
    if pr.review_mode:
        meta["review_mode"] = pr.review_mode

    # Timing
    if started_at:
        ended = datetime.now(UTC)
        meta["total_duration_seconds"] = round((ended - started_at).total_seconds(), 1)

    # Triage config (common to both paths)
    meta["triage_backend"] = config.triage_backend
    if config.triage_model:
        meta["triage_model"] = config.triage_model

    # Custom prompt paths
    prompt_paths: dict = {}
    if config.triage_prompt_path:
        prompt_paths["triage"] = config.triage_prompt_path
    if config.lightweight_review_prompt_path:
        prompt_paths["lightweight"] = config.lightweight_review_prompt_path
    if config.full_review_prompt_path:
        prompt_paths["full_review"] = config.full_review_prompt_path
    if config.reconcile_prompt_path:
        prompt_paths["reconcile"] = config.reconcile_prompt_path
    if prompt_paths:
        meta["custom_prompt_paths"] = prompt_paths

    if review_type == "lightweight":
        meta["lightweight_backend"] = config.lightweight_review_backend
        if config.lightweight_review_model:
            meta["lightweight_model"] = config.lightweight_review_model
        if lightweight_usage:
            tokens: dict = {
                "input": lightweight_usage.input_tokens,
                "output": lightweight_usage.output_tokens,
            }
            if lightweight_usage.cost_usd is not None:
                tokens["cost_usd"] = round(lightweight_usage.cost_usd, 4)
            meta["total_tokens"] = tokens
    elif review_type == "full":
        meta["enabled_reviewers"] = list(config.enabled_reviewers)
        reviewers: dict = {}
        for name in config.enabled_reviewers:
            r: dict = {}
            if name == "claude":
                if config.claude_model:
                    r["model"] = config.claude_model
                r["backend"] = config.claude_backend
            elif name == "codex":
                r["model"] = config.codex_model
                r["backend"] = config.codex_backend
            elif name == "gemini":
                if config.gemini_model:
                    r["model"] = config.gemini_model
                if config.gemini_fallback_model:
                    r["fallback_model"] = config.gemini_fallback_model
            if reviewer_outputs and name in reviewer_outputs:
                out = reviewer_outputs[name]
                r["status"] = out.status
                r["duration_seconds"] = round(out.duration_seconds, 1)
                if out.token_usage:
                    r["tokens"] = {
                        "input": out.token_usage.input_tokens,
                        "output": out.token_usage.output_tokens,
                    }
                    if out.token_usage.cost_usd is not None:
                        r["tokens"]["cost_usd"] = round(out.token_usage.cost_usd, 4)
            if r:
                reviewers[name] = r
        if reviewers:
            meta["reviewers"] = reviewers
        meta["reconciler_backend"] = config.reconciler_backend
        if config.reconciler_model:
            meta["reconciler_model"] = config.reconciler_model

        # Aggregate total tokens across all reviewers
        if reviewer_outputs:
            total = TokenUsage()
            for out in reviewer_outputs.values():
                if out.token_usage:
                    total = total + out.token_usage
            if total.input_tokens > 0 or total.output_tokens > 0:
                t: dict = {"input": total.input_tokens, "output": total.output_tokens}
                if total.cost_usd is not None:
                    t["cost_usd"] = round(total.cost_usd, 4)
                meta["total_tokens"] = t

    return meta


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
    review_decision: ReviewDecision | None,
    status_when_not_posted: str,
    previous: ProcessedState,
) -> None:
    posted_at = None
    status = status_when_not_posted
    if config.auto_submit_review_decision and review_decision is not None:
        info(f"submitting PR review decision={review_decision} {pr.url}")
        try:
            client.submit_pr_review(pr, str(output_path), review_decision)
            posted_at = ProcessedState.now_iso()
            status = "approved" if review_decision == "approve" else "changes_requested"
            info(f"submitted PR review ({status}) {pr.url}")
        except CommandError as exc:
            exc_str = str(exc)
            if (
                "Can not request changes on your own pull request" in exc_str
                or "Can not approve your own pull request" in exc_str
            ):
                warn(f"cannot submit review on own PR, falling back to comment {pr.url}")
                try:
                    client.post_pr_comment(pr, str(output_path))
                    posted_at = ProcessedState.now_iso()
                    status = "posted"
                    info(f"posted review as comment (fallback) {pr.url}")
                except Exception as fallback_exc:  # noqa: BLE001
                    warn(f"fallback comment also failed: {fallback_exc} {pr.url}")
                    status = "submission_failed"
            else:
                warn(f"failed to submit review: {exc} {pr.url}")
                status = "submission_failed"
    elif config.auto_post_review or (
        config.auto_submit_review_decision and review_decision is None
    ):
        if review_decision is None:
            info(f"no review decision (backends failed), posting as comment {pr.url}")
        else:
            info(f"posting review comment to GitHub {pr.url}")
        try:
            client.post_pr_comment(pr, str(output_path))
            posted_at = ProcessedState.now_iso()
            status = "posted"
            info(f"posted review comment {pr.url}")
        except Exception as exc:  # noqa: BLE001
            warn(f"failed to post review comment: {exc} {pr.url}")
            status = "submission_failed"
    else:
        info(
            f"auto_post_review and auto_submit_review_decision are disabled; "
            f"not posting to GitHub {pr.url}"
        )

    last_seen_rerequest_at = previous.last_seen_rerequest_at
    if pr.latest_direct_rerequest_at is not None:
        last_seen_rerequest_at = pr.latest_direct_rerequest_at

    last_slash_command_id = previous.last_slash_command_id
    if pr.slash_command_trigger is not None:
        last_slash_command_id = pr.slash_command_trigger.comment_id

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
            last_slash_command_id=last_slash_command_id,
        ),
    )
    store.save()


class _NewCommitDetected(Exception):
    """Raised when a new commit is pushed to the PR during review."""

    def __init__(self, new_sha: str) -> None:
        self.new_sha = new_sha
        super().__init__(f"new commit detected: {new_sha[:12]}")


def _check_pr_head_changed(client: GitHubClient, pr: PRCandidate) -> str | None:
    """Return the new head SHA if it differs from pr.head_sha, else None."""
    try:
        current_sha = client.get_pr_head_sha(pr)
    except Exception as exc:  # noqa: BLE001
        warn(f"failed to poll head SHA (will continue review): {exc} {pr.url}")
        return None
    if current_sha and current_sha != pr.head_sha:
        return current_sha
    return None


async def _run_reviewers_with_monitoring(
    config: AppConfig,
    client: GitHubClient,
    pr: PRCandidate,
    workdir: Path,
    progress: ProgressComment,
) -> dict[str, ReviewerOutput]:
    """Launch reviewers and poll for completion, checking for new commits periodically.

    Raises _NewCommitDetected if the PR head SHA changes while reviewers are running.
    """
    enabled_reviewers = list(config.enabled_reviewers)
    enabled_reviewer_set = set(enabled_reviewers)
    pending_tasks: dict[str, asyncio.Task] = {}

    if "claude" in enabled_reviewer_set:
        opened, reason = _circuit_is_open("claude", config.claude_model)
        if opened:
            warn(f"skipping Claude review (circuit open: {reason}) {pr.url}")
            progress.set_reviewer_skipped("claude", reason or "")
        else:
            allowed, usage_reason = _backend_has_available_usage("claude", config.claude_model)
            if not allowed:
                warn(f"skipping Claude review (usage gate: {usage_reason}) {pr.url}")
                progress.set_reviewer_skipped("claude", usage_reason or "")
            else:
                info(
                    f"starting Claude review "
                    f"(backend={config.claude_backend}, model={config.claude_model or 'default'}, "
                    f"effort={config.claude_reasoning_effort or 'default'}) {pr.url}"
                )
                pending_tasks["claude"] = _start_claude_review_task(config, pr, workdir)
                progress.set_reviewer_started("claude")
    else:
        info(f"Claude reviewer disabled {pr.url}")

    if "codex" in enabled_reviewer_set:
        opened, reason = _circuit_is_open("codex", config.codex_model)
        if opened:
            warn(f"skipping Codex review (circuit open: {reason}) {pr.url}")
            progress.set_reviewer_skipped("codex", reason or "")
        else:
            allowed, usage_reason = _backend_has_available_usage("codex", config.codex_model)
            if not allowed:
                warn(f"skipping Codex review (usage gate: {usage_reason}) {pr.url}")
                progress.set_reviewer_skipped("codex", usage_reason or "")
            else:
                info(
                    f"starting Codex review "
                    f"(backend={config.codex_backend}, model={config.codex_model}, "
                    f"effort={config.codex_reasoning_effort or 'default'}) {pr.url}"
                )
                pending_tasks["codex"] = _start_codex_review_task(config, pr, workdir)
                progress.set_reviewer_started("codex")
    else:
        info(f"Codex reviewer disabled {pr.url}")

    reviewer_models: dict[str, str | None] = {}

    if "gemini" in enabled_reviewer_set:
        gemini_available, gemini_model = _resolve_gemini_review_model(config, pr.url)
        if not gemini_available:
            warn(f"skipping Gemini review (all models exhausted) {pr.url}")
            progress.set_reviewer_skipped("gemini", "all models exhausted")
        else:
            reviewer_models["gemini"] = gemini_model
            info(f"starting Gemini review (model={gemini_model or 'default'}) {pr.url}")
            pending_tasks["gemini"] = asyncio.create_task(
                run_gemini_review(
                    pr,
                    workdir,
                    config.gemini_timeout_seconds,
                    model=gemini_model,
                    prompt_path=config.full_review_prompt_path,
                )
            )
            progress.set_reviewer_started("gemini")
    else:
        info(f"Gemini reviewer disabled {pr.url}")

    await progress.update()

    reviewer_outputs: dict[str, ReviewerOutput] = {}
    polls_since_last_sha_check = 0
    # Check for new commits roughly every 60s (every 3 poll cycles of 20s).
    sha_check_interval = 3

    try:
        while pending_tasks:
            done, _ = await asyncio.wait(
                pending_tasks.values(),
                timeout=20,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not done:
                running = ", ".join(pending_tasks.keys())
                info(f"reviewers still running ({running}) {pr.url}")

                # Periodically check for new commits.
                polls_since_last_sha_check += 1
                if (
                    config.max_mid_review_restarts > 0
                    and polls_since_last_sha_check >= sha_check_interval
                ):
                    polls_since_last_sha_check = 0
                    new_sha = await asyncio.to_thread(_check_pr_head_changed, client, pr)
                    if new_sha is not None:
                        info(
                            f"new commit detected mid-review "
                            f"({pr.head_sha[:12]} -> {new_sha[:12]}) {pr.url}"
                        )
                        raise _NewCommitDetected(new_sha)
                continue

            for reviewer_name, task in list(pending_tasks.items()):
                if task in done:
                    output = await task
                    reviewer_outputs[reviewer_name] = output
                    info(
                        f"{reviewer_name} finished "
                        f"status={output.status} duration={output.duration_seconds:.1f}s {pr.url}"
                    )
                    if reviewer_name == "codex" and output.stdout.startswith(
                        "codex JSON events captured:"
                    ):
                        info(f"{output.stdout} {pr.url}")
                    if output.status != "ok" and output.error:
                        warn(f"{reviewer_name} error: {output.error} {pr.url}")
                    # Record circuit breaker state
                    _reviewer_model = reviewer_models.get(reviewer_name) or {
                        "claude": config.claude_model,
                        "codex": config.codex_model,
                    }.get(reviewer_name)
                    if output.status == "ok":
                        _circuit_record_success(reviewer_name, _reviewer_model)
                        progress.set_reviewer_done(reviewer_name, output.duration_seconds)
                    elif output.error:
                        _circuit_record_failure(
                            reviewer_name, _reviewer_model, RuntimeError(output.error)
                        )
                        # Retry Gemini with fallback model on quota error
                        if (
                            reviewer_name == "gemini"
                            and "reset after" in output.error
                            and config.gemini_fallback_model
                            and config.gemini_fallback_model != reviewer_models.get("gemini")
                        ):
                            fb_opened, _ = _circuit_is_open("gemini", config.gemini_fallback_model)
                            if not fb_opened:
                                info(
                                    f"retrying Gemini with fallback model "
                                    f"{config.gemini_fallback_model} {pr.url}"
                                )
                                reviewer_models["gemini"] = config.gemini_fallback_model
                                pending_tasks["gemini"] = asyncio.create_task(
                                    run_gemini_review(
                                        pr,
                                        workdir,
                                        config.gemini_timeout_seconds,
                                        model=config.gemini_fallback_model,
                                        prompt_path=config.full_review_prompt_path,
                                    )
                                )
                                progress.set_reviewer_started("gemini")
                                await progress.update()
                                continue
                        progress.set_reviewer_failed(reviewer_name, output.error)
                    else:
                        progress.set_reviewer_failed(reviewer_name)
                    await progress.update()
                    pending_tasks.pop(reviewer_name)
    except _NewCommitDetected:
        # Cancel all running reviewer tasks before re-raising.
        for task in pending_tasks.values():
            task.cancel()
        # Wait briefly for cancellation to propagate.
        await asyncio.gather(*pending_tasks.values(), return_exceptions=True)
        raise

    return reviewer_outputs


def _log_token_usage(
    active_outputs: dict[str, ReviewerOutput],
    reconciler_usage: TokenUsage | None,
    pr_url: str,
) -> None:
    total = TokenUsage()
    for name, output in active_outputs.items():
        if output.token_usage is not None:
            info(
                f"token usage [{name}]: "
                f"input={output.token_usage.input_tokens:,} "
                f"output={output.token_usage.output_tokens:,}"
                f"{f' cost=${output.token_usage.cost_usd:.4f}' if output.token_usage.cost_usd is not None else ''}"
                f" {pr_url}"
            )
            total = total + output.token_usage
    if reconciler_usage is not None:
        info(
            f"token usage [reconciler]: "
            f"input={reconciler_usage.input_tokens:,} "
            f"output={reconciler_usage.output_tokens:,}"
            f"{f' cost=${reconciler_usage.cost_usd:.4f}' if reconciler_usage.cost_usd is not None else ''}"
            f" {pr_url}"
        )
        total = total + reconciler_usage
    if total.input_tokens > 0 or total.output_tokens > 0:
        info(
            f"token usage [total]: "
            f"input={total.input_tokens:,} "
            f"output={total.output_tokens:,}"
            f"{f' cost=${total.cost_usd:.4f}' if total.cost_usd is not None else ''}"
            f" {pr_url}"
        )


def _make_reviewer_summaries(
    active_outputs: dict[str, ReviewerOutput],
) -> list[ReviewerOutputSummary]:
    return [
        ReviewerOutputSummary(
            reviewer=name,
            status=output.status,
            duration_seconds=output.duration_seconds,
            error=output.error,
            token_usage=output.token_usage,
        )
        for name, output in active_outputs.items()
    ]


def _compute_total_token_usage(
    active_outputs: dict[str, ReviewerOutput],
    reconciler_usage: TokenUsage | None,
) -> TokenUsage | None:
    total = TokenUsage()
    for output in active_outputs.values():
        if output.token_usage is not None:
            total = total + output.token_usage
    if reconciler_usage is not None:
        total = total + reconciler_usage
    if total.input_tokens == 0 and total.output_tokens == 0:
        return None
    return total


async def _run_local_reviewers(
    config: AppConfig,
    pr: PRCandidate,
    workdir: Path,
) -> dict[str, ReviewerOutput]:
    """Launch reviewers without commit monitoring (for local reviews)."""
    enabled_reviewers = list(config.enabled_reviewers)
    enabled_reviewer_set = set(enabled_reviewers)
    pending_tasks: dict[str, asyncio.Task] = {}

    if "claude" in enabled_reviewer_set:
        opened, reason = _circuit_is_open("claude", config.claude_model)
        if opened:
            warn(f"skipping Claude review (circuit open: {reason})")
        else:
            allowed, usage_reason = _backend_has_available_usage("claude", config.claude_model)
            if not allowed:
                warn(f"skipping Claude review (usage gate: {usage_reason})")
            else:
                info(
                    f"starting Claude review "
                    f"(backend={config.claude_backend}, model={config.claude_model or 'default'}, "
                    f"effort={config.claude_reasoning_effort or 'default'})"
                )
                pending_tasks["claude"] = _start_claude_review_task(config, pr, workdir)

    if "codex" in enabled_reviewer_set:
        opened, reason = _circuit_is_open("codex", config.codex_model)
        if opened:
            warn(f"skipping Codex review (circuit open: {reason})")
        else:
            allowed, usage_reason = _backend_has_available_usage("codex", config.codex_model)
            if not allowed:
                warn(f"skipping Codex review (usage gate: {usage_reason})")
            else:
                info(
                    f"starting Codex review "
                    f"(backend={config.codex_backend}, model={config.codex_model})"
                )
                pending_tasks["codex"] = _start_codex_review_task(config, pr, workdir)

    reviewer_models: dict[str, str | None] = {}

    if "gemini" in enabled_reviewer_set:
        gemini_available, gemini_model = _resolve_gemini_review_model(config, pr.url)
        if not gemini_available:
            warn("skipping Gemini review (all models exhausted)")
        else:
            reviewer_models["gemini"] = gemini_model
            info(f"starting Gemini review (model={gemini_model or 'default'})")
            pending_tasks["gemini"] = asyncio.create_task(
                run_gemini_review(
                    pr,
                    workdir,
                    config.gemini_timeout_seconds,
                    model=gemini_model,
                    prompt_path=config.full_review_prompt_path,
                )
            )

    reviewer_outputs: dict[str, ReviewerOutput] = {}
    while pending_tasks:
        done, _ = await asyncio.wait(
            pending_tasks.values(),
            timeout=20,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            running = ", ".join(pending_tasks.keys())
            info(f"reviewers still running ({running})")
            continue
        for reviewer_name, task in list(pending_tasks.items()):
            if task in done:
                output = await task
                reviewer_outputs[reviewer_name] = output
                info(
                    f"{reviewer_name} finished "
                    f"status={output.status} duration={output.duration_seconds:.1f}s"
                )
                if output.status != "ok" and output.error:
                    warn(f"{reviewer_name} error: {output.error}")
                _reviewer_model = reviewer_models.get(reviewer_name) or {
                    "claude": config.claude_model,
                    "codex": config.codex_model,
                }.get(reviewer_name)
                if output.status == "ok":
                    _circuit_record_success(reviewer_name, _reviewer_model)
                elif output.error:
                    _circuit_record_failure(
                        reviewer_name, _reviewer_model, RuntimeError(output.error)
                    )
                    # Retry Gemini with fallback model on quota error
                    if (
                        reviewer_name == "gemini"
                        and "reset after" in output.error
                        and config.gemini_fallback_model
                        and config.gemini_fallback_model != reviewer_models.get("gemini")
                    ):
                        fb_opened, _ = _circuit_is_open("gemini", config.gemini_fallback_model)
                        if not fb_opened:
                            info(
                                f"retrying Gemini with fallback model "
                                f"{config.gemini_fallback_model}"
                            )
                            reviewer_models["gemini"] = config.gemini_fallback_model
                            pending_tasks["gemini"] = asyncio.create_task(
                                run_gemini_review(
                                    pr,
                                    workdir,
                                    config.gemini_timeout_seconds,
                                    model=config.gemini_fallback_model,
                                    prompt_path=config.full_review_prompt_path,
                                )
                            )
                            continue
                pending_tasks.pop(reviewer_name)

    return reviewer_outputs


def _save_prompt(
    output_dir: Path,
    pr: PRCandidate,
    stage: str,
    bundle: PromptBundle,
    version_label: str,
) -> None:
    """Save a rendered prompt bundle as a stage markdown file."""
    write_stage_markdown(
        output_dir,
        pr,
        f"{stage}.prompt",
        format_prompt_bundle(bundle),
        version_label=version_label,
    )


async def process_local_review(
    config: AppConfig,
    pr: PRCandidate,
    workdir: Path,
) -> ProcessingResult:
    """Run review pipeline on a local repo without GitHub interactions."""
    info(f"processing local review: {pr.title}")

    try:
        # Triage
        triage_result, triage_bundle = await run_triage(
            pr,
            workdir,
            config.triage_timeout_seconds,
            backend=config.triage_backend,
            model=config.triage_model,
            prompt_path=config.triage_prompt_path,
            claude_backend=config.claude_backend,
            gemini_fallback_model=config.gemini_fallback_model,
        )

        if triage_result == TriageResult.SIMPLE:
            try:
                (
                    lightweight_text,
                    lightweight_usage,
                    lightweight_bundle,
                ) = await run_lightweight_review(
                    pr,
                    workdir,
                    config.lightweight_review_timeout_seconds,
                    backend=config.lightweight_review_backend,
                    model=config.lightweight_review_model,
                    reasoning_effort=config.lightweight_review_reasoning_effort,
                    prompt_path=config.lightweight_review_prompt_path,
                    claude_backend=config.claude_backend,
                    gemini_fallback_model=config.gemini_fallback_model,
                )
            except PromptOverrideError:
                raise
            except Exception as exc:  # noqa: BLE001
                warn(f"lightweight review failed, falling back to full review: {exc}")
                triage_result = TriageResult.FULL_REVIEW

        if triage_result == TriageResult.SIMPLE:
            lightweight_text = _validate_review_format(
                lightweight_text,
                pr_url=pr.url,
                injection_protection=config.prompt_injection_protection,
            )

            return ProcessingResult(
                processed=True,
                pr_url=pr.url,
                pr_key=pr.key,
                status="lightweight_generated",
                final_review=lightweight_text,
                triage_result="simple",
                total_token_usage=lightweight_usage,
            )

        # Full review path — inject skills for local reviews
        remote_skill_paths: list[Path] = []
        if config.skills:
            try:
                remote_skill_paths = await fetch_remote_skills(
                    config.skills, Path(config.output_dir).parent
                )
            except Exception as exc:  # noqa: BLE001
                warn(f"failed to fetch remote skills: {exc}")
                raise
        local_skills_dir = workdir / "skills"
        if local_skills_dir.is_dir():
            await asyncio.to_thread(inject_skills, local_skills_dir, workdir)
        if remote_skill_paths:
            await asyncio.to_thread(inject_skill_paths, remote_skill_paths, workdir)

        try:
            reviewer_outputs = await _run_local_reviewers(config, pr, workdir)
        finally:
            await asyncio.to_thread(remove_injected_skills, workdir)
        enabled_reviewers = list(config.enabled_reviewers)
        active_outputs = {
            name: reviewer_outputs.get(name, _disabled_output(name)) for name in enabled_reviewers
        }

        ok_outputs = _successful_outputs(active_outputs)
        failed_names = [n for n in active_outputs if n not in ok_outputs]
        if failed_names:
            warn(f"excluding failed reviewers from reconciliation: {', '.join(failed_names)}")

        if len(ok_outputs) >= 2:
            (
                reconciler_backend,
                reconciler_timeout_seconds,
                reconciler_model,
                reconciler_reasoning_effort,
            ) = _resolve_reconciler_settings(config)
            info(f"reconciling outputs (backend={' > '.join(reconciler_backend)})")
            final_review, reconciler_usage, reconcile_bundle = await reconcile_reviews(
                pr,
                workdir,
                list(ok_outputs.values()),
                reconciler_timeout_seconds,
                reconciler_backend=reconciler_backend,
                reconciler_model=reconciler_model,
                reconciler_reasoning_effort=reconciler_reasoning_effort,
                max_findings=config.max_findings,
                max_test_gaps=config.max_test_gaps,
                prompt_path=config.reconcile_prompt_path,
                claude_backend=config.claude_backend,
                gemini_fallback_model=config.gemini_fallback_model,
            )
            final_review = _validate_review_format(
                final_review, pr_url=pr.url, injection_protection=config.prompt_injection_protection
            )
        elif len(ok_outputs) == 1:
            sole_name = next(iter(ok_outputs))
            info(f"single reviewer mode ({sole_name})")
            final_review = _validate_review_format(
                _single_reviewer_final_review(ok_outputs[sole_name]),
                pr_url=pr.url,
                injection_protection=config.prompt_injection_protection,
            )
            reconciler_usage = None
        elif len(enabled_reviewers) >= 1:
            warn("all reviewers failed, no review produced")
            final_review = _all_failed_review()
            reconciler_usage = None
        else:
            raise RuntimeError("No enabled reviewers configured")

        _log_token_usage(active_outputs, reconciler_usage, pr.url)

        review_decision = infer_review_decision(final_review) if ok_outputs else None
        return ProcessingResult(
            processed=True,
            pr_url=pr.url,
            pr_key=pr.key,
            status="generated",
            final_review=final_review,
            triage_result="full_review",
            review_decision=review_decision,
            reviewer_outputs=_make_reviewer_summaries(active_outputs),
            total_token_usage=_compute_total_token_usage(active_outputs, reconciler_usage),
        )
    except Exception as exc:  # noqa: BLE001
        warn(f"failed processing local review: {exc}")
        return ProcessingResult(
            processed=False,
            pr_url=pr.url,
            pr_key=pr.key,
            status="error",
            error=str(exc),
        )


async def process_candidate(
    config: AppConfig,
    client: GitHubClient,
    store: StateStore,
    workspace_mgr: PRWorkspace,
    pr: PRCandidate,
    *,
    use_saved_review: bool = False,
    verbose: bool = True,
) -> ProcessingResult:
    def detail(message: str) -> None:
        if verbose:
            info(message)

    detail(f"processing {pr.title} {pr.url}")

    if config.skip_own_prs and pr.author_login == client.viewer_login:
        detail(f"skipping own PR (author={pr.author_login}) {pr.url}")
        return ProcessingResult(
            processed=False,
            pr_url=pr.url,
            pr_key=pr.key,
            status="skipped_own_pr",
        )

    previous = store.get(pr.key)

    if use_saved_review:
        saved_review_path = _existing_saved_review_path(Path(config.output_dir), pr, previous)
        if saved_review_path is None:
            detail(f"skipping, use_saved_review requested but no saved review exists {pr.url}")
            previous.last_status = "skipped_missing_saved_review"
            previous.trigger_mode = config.trigger_mode
            store.set(pr.key, previous)
            await asyncio.to_thread(store.save)
            return ProcessingResult(
                processed=False,
                pr_url=pr.url,
                pr_key=pr.key,
                status="skipped_missing_saved_review",
            )
        detail(f"using saved review file ({saved_review_path}) {pr.url}")
        saved_review_text = await asyncio.to_thread(saved_review_path.read_text, encoding="utf-8")
        saved_decision = infer_review_decision(saved_review_text)
        await asyncio.to_thread(
            _publish_and_persist,
            config,
            client,
            store,
            pr,
            saved_review_path,
            saved_decision,
            status_when_not_posted="reused_saved_review",
            previous=previous,
        )
        info(f"processing complete (reused saved review) {pr.url}")
        return ProcessingResult(
            processed=True,
            pr_url=pr.url,
            pr_key=pr.key,
            status="reused_saved_review",
            final_review=saved_review_text,
            output_file=str(saved_review_path.resolve()),
        )

    # Auto-reuse: if we already generated a review for this exact head SHA but
    # submission failed, reuse the saved review instead of re-running reviewers.
    if (
        previous.last_reviewed_head_sha == pr.head_sha
        and previous.last_output_file
        and previous.last_status == "submission_failed"
    ):
        saved_path = Path(previous.last_output_file)
        if saved_path.exists():
            info(f"reusing previously generated review (submission retry) {pr.url}")
            review_text = await asyncio.to_thread(saved_path.read_text, encoding="utf-8")
            retry_decision = infer_review_decision(review_text)
            await asyncio.to_thread(
                _publish_and_persist,
                config,
                client,
                store,
                pr,
                saved_path,
                retry_decision,
                status_when_not_posted="reused_saved_review",
                previous=previous,
            )
            info(f"processing complete (submission retry) {pr.url}")
            return ProcessingResult(
                processed=True,
                pr_url=pr.url,
                pr_key=pr.key,
                status="reused_saved_review",
                final_review=review_text,
                output_file=str(saved_path.resolve()),
            )

    if pr.slash_command_trigger is not None:
        trigger = pr.slash_command_trigger

        try:
            await asyncio.to_thread(
                client.add_reaction_to_comment, pr.owner, pr.repo, trigger.comment_id, "eyes"
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"{pr.key}: failed to react to /review comment: {exc}")

        already_reviewed = (
            not trigger.force
            and previous.last_reviewed_head_sha == pr.head_sha
            and previous.last_status in ("posted", "approved", "changes_requested", "generated")
        )
        if already_reviewed:
            try:
                await asyncio.to_thread(
                    client.post_pr_comment_inline,
                    pr,
                    "Already reviewed at this commit. Push new changes or use "
                    "`/review force` to re-review.",
                )
            except Exception as exc:  # noqa: BLE001
                warn(f"{pr.key}: failed to post already-reviewed reply: {exc}")

            previous.last_slash_command_id = trigger.comment_id
            store.set(pr.key, previous)
            await asyncio.to_thread(store.save)
            return ProcessingResult(
                processed=False,
                pr_url=pr.url,
                pr_key=pr.key,
                status="skipped_already_reviewed",
            )

    else:
        decision = _compute_processing_decision(previous, pr, config.trigger_mode)
        if decision.should_process:
            detail(f"trigger check passed ({decision.reason}) {pr.url}")
        else:
            detail(f"skipping, trigger check skipped ({decision.reason}) {pr.url}")
            previous.last_status = f"skipped_{decision.reason}"
            previous.trigger_mode = config.trigger_mode
            store.set(pr.key, previous)
            await asyncio.to_thread(store.save)
            return ProcessingResult(
                processed=False,
                pr_url=pr.url,
                pr_key=pr.key,
                status=f"skipped_{decision.reason}",
            )

        try:
            await asyncio.to_thread(client.add_eyes_reaction, pr)
        except Exception as exc:  # noqa: BLE001
            warn(f"{pr.key}: failed to add eyes reaction: {exc}")

    progress = ProgressComment(client, pr)
    if not pr.is_local:
        await progress.create()

    workdir: Path | None = None
    output_path: Path | None = None
    restarts_remaining = config.max_mid_review_restarts
    review_started_at = datetime.now(UTC)
    try:
        info(f"preparing workspace {pr.url}")
        workdir = await asyncio.to_thread(workspace_mgr.prepare, pr)
        info(f"workspace ready at {workdir} {pr.url}")

        # Fetch remote skills (once, reused across restarts)
        remote_skill_paths: list[Path] = []
        if config.skills:
            try:
                remote_skill_paths = await fetch_remote_skills(
                    config.skills, Path(config.output_dir).parent
                )
            except Exception as exc:  # noqa: BLE001
                warn(f"failed to fetch remote skills: {exc} {pr.url}")
                raise

        # Fetch PR comments for prompt context
        if not pr.is_local and pr.number > 0:
            try:
                pr.pr_comments = await asyncio.to_thread(client.get_pr_issue_comments, pr)
            except Exception as exc:  # noqa: BLE001
                warn(f"failed to fetch PR comments: {exc} {pr.url}")

        # Triage: classify PR as simple or full_review
        triage_result, triage_bundle = await run_triage(
            pr,
            workdir,
            config.triage_timeout_seconds,
            backend=config.triage_backend,
            model=config.triage_model,
            prompt_path=config.triage_prompt_path,
            claude_backend=config.claude_backend,
            gemini_fallback_model=config.gemini_fallback_model,
        )

        if triage_result == TriageResult.SIMPLE:
            progress.set_triage_done("lightweight")
        else:
            progress.set_triage_done("full", enabled_reviewers=list(config.enabled_reviewers))
        await progress.update()

        if triage_result == TriageResult.SIMPLE:
            # Lightweight review path
            try:
                # Check for new commits before starting lightweight review
                if config.max_mid_review_restarts > 0:
                    new_sha = await asyncio.to_thread(_check_pr_head_changed, client, pr)
                    if new_sha is not None:
                        info(
                            f"new commit detected before lightweight review "
                            f"({pr.head_sha[:12]} -> {new_sha[:12]}), "
                            f"updating {pr.url}"
                        )
                        pr.head_sha = new_sha
                        await asyncio.to_thread(workspace_mgr.update_to_latest, workdir, pr)

                progress.set_review_started()
                await progress.update()
                lightweight_start = datetime.now(UTC)

                (
                    lightweight_text,
                    lightweight_usage,
                    lightweight_bundle,
                ) = await run_lightweight_review(
                    pr,
                    workdir,
                    config.lightweight_review_timeout_seconds,
                    backend=config.lightweight_review_backend,
                    model=config.lightweight_review_model,
                    reasoning_effort=config.lightweight_review_reasoning_effort,
                    prompt_path=config.lightweight_review_prompt_path,
                    claude_backend=config.claude_backend,
                    gemini_fallback_model=config.gemini_fallback_model,
                )

                lightweight_duration = (datetime.now(UTC) - lightweight_start).total_seconds()
                progress.set_review_done(lightweight_duration)
                await progress.update()
            except PromptOverrideError:
                raise
            except Exception as exc:  # noqa: BLE001
                warn(f"lightweight review failed, falling back to full review: {exc} {pr.url}")
                triage_result = TriageResult.FULL_REVIEW

        if triage_result == TriageResult.SIMPLE:
            lightweight_text = _validate_review_format(
                lightweight_text,
                pr_url=pr.url,
                injection_protection=config.prompt_injection_protection,
            )

            if lightweight_usage is not None:
                cost = lightweight_usage.cost_usd
                cost_str = f" cost=${cost:.4f}" if cost is not None else ""
                info(
                    f"token usage [lightweight]: "
                    f"input={lightweight_usage.input_tokens:,} "
                    f"output={lightweight_usage.output_tokens:,}"
                    f"{cost_str} {pr.url}"
                )

            info(f"writing lightweight review output {pr.url}")
            version_label = _output_version_label(pr)
            output_path = await asyncio.to_thread(
                write_review_markdown,
                Path(config.output_dir),
                pr,
                lightweight_text,
                version_label=version_label,
            )
            await asyncio.to_thread(
                write_stage_markdown,
                Path(config.output_dir),
                pr,
                "lightweight",
                lightweight_text,
                version_label=version_label,
            )
            await asyncio.to_thread(
                write_review_meta,
                Path(config.output_dir),
                pr,
                _build_review_meta(
                    pr,
                    config,
                    "lightweight",
                    triage_result="simple",
                    lightweight_usage=lightweight_usage,
                    started_at=review_started_at,
                ),
                version_label=version_label,
            )
            info(f"Lightweight review ready: {output_path.resolve()}")

            # Save prompts
            output_dir = Path(config.output_dir)
            await asyncio.to_thread(
                _save_prompt, output_dir, pr, "triage", triage_bundle, version_label
            )
            await asyncio.to_thread(
                _save_prompt, output_dir, pr, "lightweight", lightweight_bundle, version_label
            )

            await asyncio.to_thread(
                _publish_and_persist,
                config,
                client,
                store,
                pr,
                output_path,
                infer_review_decision(lightweight_text),
                status_when_not_posted="lightweight_generated",
                previous=previous,
            )
            info(f"processing complete (lightweight) {pr.url}")
            return ProcessingResult(
                processed=True,
                pr_url=pr.url,
                pr_key=pr.key,
                status="lightweight_generated",
                final_review=lightweight_text,
                output_file=str(output_path.resolve()),
                triage_result="simple",
                total_token_usage=lightweight_usage,
            )

        # Full review path continues below
        # Inject skills into the workspace for full reviews.
        local_skills_dir = workdir / "skills"
        if local_skills_dir.is_dir():
            await asyncio.to_thread(inject_skills, local_skills_dir, workdir)
        if remote_skill_paths:
            await asyncio.to_thread(inject_skill_paths, remote_skill_paths, workdir)

        # Retry loop: restart reviewers when new commits are pushed mid-review.
        while True:
            try:
                reviewer_outputs = await _run_reviewers_with_monitoring(
                    config, client, pr, workdir, progress
                )
                break  # Reviews completed without mid-review commits.
            except _NewCommitDetected as ncd:
                if restarts_remaining <= 0:
                    warn(
                        f"max mid-review restarts ({config.max_mid_review_restarts}) "
                        f"exhausted; proceeding with stale review results {pr.url}"
                    )
                    # Fall through with whatever outputs we have (empty dict).
                    reviewer_outputs = {}
                    break
                restarts_remaining -= 1
                info(
                    f"restarting review on new head {ncd.new_sha[:12]} "
                    f"({restarts_remaining} restart(s) remaining) {pr.url}"
                )
                # Clean injected skills before workspace update to avoid
                # untracked-file conflicts with git checkout.
                await asyncio.to_thread(remove_injected_skills, workdir)
                pr.head_sha = ncd.new_sha
                await asyncio.to_thread(workspace_mgr.update_to_latest, workdir, pr)
                # Re-inject skills after workspace update.
                if local_skills_dir.is_dir():
                    await asyncio.to_thread(inject_skills, local_skills_dir, workdir)
                if remote_skill_paths:
                    await asyncio.to_thread(inject_skill_paths, remote_skill_paths, workdir)
                info(f"workspace updated to {ncd.new_sha[:12]} {pr.url}")

        enabled_reviewers = list(config.enabled_reviewers)

        active_outputs = {
            name: reviewer_outputs.get(name, _disabled_output(name)) for name in enabled_reviewers
        }

        ok_outputs = _successful_outputs(active_outputs)
        failed_names = [n for n in active_outputs if n not in ok_outputs]
        if failed_names:
            warn(
                f"excluding failed reviewers from reconciliation: "
                f"{', '.join(failed_names)} {pr.url}"
            )

        if len(ok_outputs) >= 2:
            reviewer_names = " + ".join(ok_outputs)
            (
                reconciler_backend,
                reconciler_timeout_seconds,
                reconciler_model,
                reconciler_reasoning_effort,
            ) = _resolve_reconciler_settings(config)
            primary_backend = reconciler_backend[0]
            effort_label = (
                reconciler_reasoning_effort or "default" if primary_backend != "gemini" else "n/a"
            )
            info(
                f"reconciling {reviewer_names} outputs "
                f"(backend={' > '.join(reconciler_backend)}, "
                f"model={reconciler_model or 'default'}, "
                f"effort={effort_label}) {pr.url}"
            )
            progress.set_reconciliation_started()
            await progress.update()
            reconcile_start = datetime.now(UTC)
            final_review, reconciler_usage, reconcile_bundle = await reconcile_reviews(
                pr,
                workdir,
                list(ok_outputs.values()),
                reconciler_timeout_seconds,
                reconciler_backend=reconciler_backend,
                reconciler_model=reconciler_model,
                reconciler_reasoning_effort=reconciler_reasoning_effort,
                max_findings=config.max_findings,
                max_test_gaps=config.max_test_gaps,
                prompt_path=config.reconcile_prompt_path,
                claude_backend=config.claude_backend,
                gemini_fallback_model=config.gemini_fallback_model,
            )
            reconcile_duration = (datetime.now(UTC) - reconcile_start).total_seconds()
            progress.set_reconciliation_done(reconcile_duration)
            await progress.update()
            final_review = _validate_review_format(
                final_review, pr_url=pr.url, injection_protection=config.prompt_injection_protection
            )
        elif len(ok_outputs) == 1:
            sole_name = next(iter(ok_outputs))
            info(f"single reviewer mode ({sole_name}) {pr.url}")
            final_review = _validate_review_format(
                _single_reviewer_final_review(ok_outputs[sole_name]),
                pr_url=pr.url,
                injection_protection=config.prompt_injection_protection,
            )
            reconciler_usage = None
            progress.set_reconciliation_skipped()
            await progress.update()
        elif len(enabled_reviewers) >= 1:
            warn(f"all reviewers failed, no review produced {pr.url}")
            final_review = _all_failed_review()
            reconciler_usage = None
            progress.set_reconciliation_skipped()
            await progress.update()
        else:
            raise RuntimeError("No enabled reviewers configured")

        _log_token_usage(active_outputs, reconciler_usage, pr.url)
        info(f"writing final markdown output {pr.url}")
        version_label = _output_version_label(pr)
        output_path = await asyncio.to_thread(
            write_review_markdown,
            Path(config.output_dir),
            pr,
            final_review,
            version_label=version_label,
        )
        # Write per-stage files for each successful reviewer
        output_dir = Path(config.output_dir)
        for name, output in active_outputs.items():
            if output.status == "ok":
                await asyncio.to_thread(
                    write_stage_markdown,
                    output_dir,
                    pr,
                    name,
                    output.markdown,
                    version_label=version_label,
                )
                if output.conversation:
                    await asyncio.to_thread(
                        write_conversation_jsonl,
                        output_dir,
                        pr,
                        name,
                        output.conversation,
                        version_label=version_label,
                    )
        # Write reconciliation output when multiple reviewers contributed
        if len(ok_outputs) >= 2:
            await asyncio.to_thread(
                write_stage_markdown,
                output_dir,
                pr,
                "reconcile",
                final_review,
                version_label=version_label,
            )
        await asyncio.to_thread(
            write_review_meta,
            output_dir,
            pr,
            _build_review_meta(
                pr,
                config,
                "full",
                triage_result="complex",
                reviewer_outputs=active_outputs,
                started_at=review_started_at,
            ),
            version_label=version_label,
        )
        # Save prompts
        await asyncio.to_thread(
            _save_prompt, output_dir, pr, "triage", triage_bundle, version_label
        )
        for name, output in active_outputs.items():
            if output.status == "ok" and output.prompt:
                await asyncio.to_thread(
                    _save_prompt,
                    output_dir,
                    pr,
                    name,
                    PromptBundle(prompt=output.prompt, system_prompt=output.system_prompt),
                    version_label,
                )
        if len(ok_outputs) >= 2:
            await asyncio.to_thread(
                _save_prompt, output_dir, pr, "reconcile", reconcile_bundle, version_label
            )
        info(f"Final review ready: {output_path.resolve()}")

        review_decision = infer_review_decision(final_review) if ok_outputs else None
        await asyncio.to_thread(
            _publish_and_persist,
            config,
            client,
            store,
            pr,
            output_path,
            review_decision,
            status_when_not_posted="generated",
            previous=previous,
        )
        info(f"processing complete {pr.url}")
        return ProcessingResult(
            processed=True,
            pr_url=pr.url,
            pr_key=pr.key,
            status="generated",
            final_review=final_review,
            output_file=str(output_path.resolve()),
            triage_result="full_review",
            review_decision=review_decision,
            reviewer_outputs=_make_reviewer_summaries(active_outputs),
            total_token_usage=_compute_total_token_usage(active_outputs, reconciler_usage),
        )
    except Exception as exc:  # noqa: BLE001
        warn(f"failed processing: {exc} {pr.url}")
        state = store.get(pr.key)
        state.last_status = f"error: {exc}"
        state.trigger_mode = config.trigger_mode
        if output_path is not None and output_path.exists():
            state.last_output_file = str(output_path.resolve())
            state.last_reviewed_head_sha = pr.head_sha
            state.last_processed_at = ProcessedState.now_iso()
        store.set(pr.key, state)
        await asyncio.to_thread(store.save)
        return ProcessingResult(
            processed=False,
            pr_url=pr.url,
            pr_key=pr.key,
            status="error",
            error=str(exc),
        )
    finally:
        if workdir is not None:
            await asyncio.to_thread(workspace_mgr.cleanup, workdir)
