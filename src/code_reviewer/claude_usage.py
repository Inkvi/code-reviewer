from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from code_reviewer.backend_usage import (
    BackendUsageAnswer,
    BackendUsageDecision,
    BackendUsageSnapshot,
    BackendUsageWindow,
    ask_backend_usage_question,
    decide_backend_usage,
    has_enough_backend_usage,
    load_backend_usage_snapshot,
)

ClaudeRateLimitEvent = BackendUsageWindow
ClaudeUsageSnapshot = BackendUsageSnapshot
ClaudeBackendDecision = BackendUsageDecision
ClaudeUsageAnswer = BackendUsageAnswer


def load_claude_usage_snapshot(
    support_dir: Path | None = None,
    *,
    auth_status_loader: Callable[[list[str]], object] | None = None,
) -> ClaudeUsageSnapshot:
    return load_backend_usage_snapshot(
        "claude",
        support_dir,
        auth_status_loader=auth_status_loader,
    )


def decide_claude_backend_usage(
    snapshot: ClaudeUsageSnapshot,
    *,
    now: datetime | None = None,
    minimum_remaining_percent: float = 10.0,
) -> ClaudeBackendDecision:
    return decide_backend_usage(
        snapshot,
        now=now,
        minimum_remaining_percent=minimum_remaining_percent,
    )


def has_enough_claude_usage(
    *,
    minimum_remaining_percent: float = 10.0,
    snapshot: ClaudeUsageSnapshot | None = None,
    support_dir: Path | None = None,
    now: datetime | None = None,
    auth_status_loader: Callable[[list[str]], object] | None = None,
) -> bool:
    return has_enough_backend_usage(
        "claude",
        minimum_remaining_percent=minimum_remaining_percent,
        snapshot=snapshot,
        support_dir=support_dir,
        now=now,
        auth_status_loader=auth_status_loader,
    )


def ask_claude_usage_question(
    question: str,
    *,
    snapshot: ClaudeUsageSnapshot | None = None,
    support_dir: Path | None = None,
    now: datetime | None = None,
    minimum_remaining_percent: float = 10.0,
    auth_status_loader: Callable[[list[str]], object] | None = None,
) -> ClaudeUsageAnswer:
    return ask_backend_usage_question(
        "claude",
        question,
        snapshot=snapshot,
        support_dir=support_dir,
        now=now,
        minimum_remaining_percent=minimum_remaining_percent,
        auth_status_loader=auth_status_loader,
    )
