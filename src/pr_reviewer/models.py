from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class SlashCommandTrigger:
    comment_id: int
    comment_author: str
    comment_created_at: str
    force: bool = False


@dataclass(slots=True)
class PRCandidate:
    owner: str
    repo: str
    number: int
    url: str
    title: str
    author_login: str
    base_ref: str
    head_sha: str
    updated_at: str
    latest_direct_rerequest_at: str | None = None
    trigger_metadata_version: int = 1
    additions: int = 0
    deletions: int = 0
    changed_file_paths: list[str] = field(default_factory=list)
    slash_command_trigger: SlashCommandTrigger | None = None

    @property
    def key(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None

    def __add__(self, other: TokenUsage) -> TokenUsage:
        cost: float | None = None
        if self.cost_usd is not None or other.cost_usd is not None:
            cost = (self.cost_usd or 0.0) + (other.cost_usd or 0.0)
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=cost,
        )


@dataclass(slots=True)
class ReviewerOutput:
    reviewer: str
    status: str
    markdown: str
    stdout: str
    stderr: str
    error: str | None
    started_at: datetime
    ended_at: datetime
    token_usage: TokenUsage | None = None

    @property
    def duration_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()


@dataclass(slots=True)
class ProcessedState:
    # Legacy field kept for backward-compatibility with existing state files.
    last_reviewed_head_sha: str | None = None
    last_processed_at: str | None = None
    last_seen_rerequest_at: str | None = None
    trigger_mode: str = "rerequest_only"
    last_output_file: str | None = None
    last_status: str | None = None
    last_posted_at: str | None = None
    last_slash_command_id: int | None = None

    @staticmethod
    def now_iso() -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat()
