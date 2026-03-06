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
    is_local: bool = False
    review_mode: str | None = None

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
class ReviewerOutputSummary:
    reviewer: str
    status: str
    duration_seconds: float
    error: str | None = None
    token_usage: TokenUsage | None = None


@dataclass(slots=True)
class ProcessingResult:
    processed: bool
    pr_url: str
    pr_key: str
    status: str
    final_review: str | None = None
    output_file: str | None = None
    triage_result: str | None = None
    review_decision: str | None = None
    reviewer_outputs: list[ReviewerOutputSummary] | None = None
    total_token_usage: TokenUsage | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "processed": self.processed,
            "pr_url": self.pr_url,
            "pr_key": self.pr_key,
            "status": self.status,
        }
        if self.final_review is not None:
            d["final_review"] = self.final_review
        if self.output_file is not None:
            d["output_file"] = self.output_file
        if self.triage_result is not None:
            d["triage_result"] = self.triage_result
        if self.review_decision is not None:
            d["review_decision"] = self.review_decision
        if self.reviewer_outputs is not None:
            d["reviewer_outputs"] = [
                {
                    "reviewer": ro.reviewer,
                    "status": ro.status,
                    "duration_seconds": ro.duration_seconds,
                    "error": ro.error,
                    **(
                        {
                            "token_usage": {
                                "input_tokens": ro.token_usage.input_tokens,
                                "output_tokens": ro.token_usage.output_tokens,
                                "cost_usd": ro.token_usage.cost_usd,
                            }
                        }
                        if ro.token_usage is not None
                        else {}
                    ),
                }
                for ro in self.reviewer_outputs
            ]
        if self.total_token_usage is not None:
            d["total_token_usage"] = {
                "input_tokens": self.total_token_usage.input_tokens,
                "output_tokens": self.total_token_usage.output_tokens,
                "cost_usd": self.total_token_usage.cost_usd,
            }
        if self.error is not None:
            d["error"] = self.error
        return d


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
