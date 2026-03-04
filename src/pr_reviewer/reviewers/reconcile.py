from __future__ import annotations

from pathlib import Path

from pr_reviewer.models import PRCandidate, ReviewerOutput
from pr_reviewer.reviewers.claude_sdk import _run_claude_prompt
from pr_reviewer.reviewers.codex_cli import run_codex_prompt
from pr_reviewer.reviewers.gemini_cli import run_gemini_prompt


def _format_source(name: str, output: ReviewerOutput) -> str:
    if output.status != "ok":
        return f"{name} failed: {output.error or 'unknown error'}"
    return output.markdown or f"{name} returned no content"


def _format_pr_comments(pr_comments: list[str] | None) -> str:
    if not pr_comments:
        return "_None provided._"
    sections = [f"- {entry}" for entry in pr_comments]
    return "\n".join(sections)


async def reconcile_reviews(
    pr: PRCandidate,
    workspace: Path,
    reviewer_outputs: list[ReviewerOutput],
    timeout_seconds: int,
    *,
    reconciler_backend: str = "claude",
    pr_comments: list[str] | None = None,
    reconciler_model: str | None = None,
    reconciler_reasoning_effort: str | None = None,
) -> str:
    source_sections: list[str] = []
    for i, output in enumerate(reviewer_outputs):
        letter = chr(ord("A") + i)
        label = output.reviewer.capitalize()
        formatted = _format_source(label, output)
        source_sections.append(f"Source {letter} ({label}):\n{formatted}")

    sources_text = "\n\n".join(source_sections)
    count = len(reviewer_outputs)
    comments_text = _format_pr_comments(pr_comments)

    prompt = f"""
You are reconciling {count} PR reviews into one final markdown review that will be posted
directly as a GitHub comment.

PR:
- URL: {pr.url}
- Title: {pr.title}
- Base: {pr.base_ref}
- Head SHA: {pr.head_sha}

PR issue-thread comments to consider for additional context:
{comments_text}

{sources_text}

Your primary job is validation, not aggregation. Treat all findings as suspects:
- Reviewers can be wrong. Findings may be hallucinated, misread, or based on incorrect assumptions.
- Overlapping findings across reviewers do not automatically mean they are correct.
- For each finding, verify it is supported by actual evidence in the code context.
  Discard any finding you cannot confirm.
- Only include findings you are confident are real issues.

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
- Do not use tools.
""".strip()

    if reconciler_backend == "claude":
        return await _run_claude_prompt(
            prompt,
            workspace,
            timeout_seconds,
            system_prompt=(
                "You are a code review reconciler. Respond only with the requested markdown "
                "sections. Do not use any tools."
            ),
            max_turns=1,
            model=reconciler_model,
            reasoning_effort=reconciler_reasoning_effort,
        )
    if reconciler_backend == "codex":
        return await run_codex_prompt(
            prompt,
            workspace,
            timeout_seconds,
            model=reconciler_model,
            reasoning_effort=reconciler_reasoning_effort,
        )
    if reconciler_backend == "gemini":
        return await run_gemini_prompt(
            prompt,
            workspace,
            timeout_seconds,
            model=reconciler_model,
        )
    raise RuntimeError(f"Unsupported reconciler backend: {reconciler_backend}")
