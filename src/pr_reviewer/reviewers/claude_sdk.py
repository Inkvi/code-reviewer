from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from anyio import fail_after
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

from pr_reviewer.models import PRCandidate, ReviewerOutput


def _collect_text_from_assistant(message: AssistantMessage) -> str:
    chunks: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            chunks.append(block.text)
    return "\n".join(chunks)


async def _run_claude_prompt(
    prompt: str,
    cwd: Path,
    timeout_seconds: int,
    *,
    system_prompt: str | None = None,
    max_turns: int = 20,
) -> str:
    options = ClaudeAgentOptions(
        cwd=cwd,
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        system_prompt=system_prompt,
    )

    parts: list[str] = []
    final_result: str | None = None

    with fail_after(timeout_seconds):
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                text = _collect_text_from_assistant(message)
                if text.strip():
                    parts.append(text)
            elif isinstance(message, ResultMessage):
                if message.result:
                    final_result = message.result

    merged = (final_result or "\n".join(parts)).strip()
    if not merged:
        raise RuntimeError("Claude returned an empty response")
    return merged


async def run_claude_review(
    pr: PRCandidate, workspace: Path, timeout_seconds: int
) -> ReviewerOutput:
    started = datetime.now(UTC)
    try:
        prompt = f"/review {pr.url}\n\nReview this PR and produce a concise markdown review."
        markdown = await _run_claude_prompt(prompt, workspace, timeout_seconds)
        status = "ok"
        error = None
        stderr = ""
    except Exception as exc:  # noqa: BLE001
        markdown = ""
        status = "error"
        error = str(exc)
        stderr = str(exc)

    ended = datetime.now(UTC)
    return ReviewerOutput(
        reviewer="claude",
        status=status,
        markdown=markdown,
        stdout=markdown,
        stderr=stderr,
        error=error,
        started_at=started,
        ended_at=ended,
    )
