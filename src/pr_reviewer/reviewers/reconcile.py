from __future__ import annotations

from pathlib import Path

from pr_reviewer.models import PRCandidate, ReviewerOutput
from pr_reviewer.reviewers.claude_sdk import _run_claude_prompt


def _format_source(name: str, output: ReviewerOutput) -> str:
    if output.status != "ok":
        return f"{name} failed: {output.error or 'unknown error'}"
    return output.markdown or f"{name} returned no content"


async def reconcile_reviews(
    pr: PRCandidate,
    workspace: Path,
    claude_output: ReviewerOutput,
    codex_output: ReviewerOutput,
    timeout_seconds: int,
) -> str:
    source_claude = _format_source("Claude", claude_output)
    source_codex = _format_source("Codex", codex_output)

    prompt = f"""
You are reconciling two PR reviews into one final markdown review.

PR:
- URL: {pr.url}
- Title: {pr.title}
- Base: {pr.base_ref}
- Head SHA: {pr.head_sha}

Source A (Claude):
{source_claude}

Source B (Codex):
{source_codex}

Produce a final markdown review with:
1) Findings (highest severity first)
2) Open questions (if any)
3) Test gaps
Do not invent evidence. If uncertain, say so.
""".strip()

    return await _run_claude_prompt(prompt, workspace, timeout_seconds)
