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
You are reconciling two PR reviews into one final markdown review that will be posted directly as
a GitHub comment.

PR:
- URL: {pr.url}
- Title: {pr.title}
- Base: {pr.base_ref}
- Head SHA: {pr.head_sha}

Source A (Claude):
{source_claude}

Source B (Codex):
{source_codex}

Strict output rules:
- Keep total output under 220 words.
- No tables, no long summary, no praise/filler.
- Include only these sections in this exact order:
  1) `### Findings`
  2) `### Test Gaps`
- `### Findings`:
  - 0-5 bullets, highest severity first.
  - Each bullet format:
    `- [P1|P2|P3] path[:line] - issue. Impact. Recommended fix.`
  - If no material issues, write exactly:
    `- No material findings.`
- `### Test Gaps`:
  - 0-3 bullets with concrete missing tests.
  - If none, write:
    `- None noted.`
- Do not include a verdict section. Automation decides approve/request-changes from severity tags.
- Do not invent evidence. If uncertain, omit.
""".strip()

    return await _run_claude_prompt(
        prompt,
        workspace,
        timeout_seconds,
        system_prompt="You are a code review reconciler. Respond only with the requested markdown sections. Do not use any tools.",
        max_turns=1,
    )
