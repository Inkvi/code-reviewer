from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from anyio import fail_after
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ProcessError,
    ResultMessage,
    TextBlock,
    query,
)

from code_reviewer.models import PRCandidate, ReviewerOutput, TokenUsage


def _collect_text_from_assistant(message: AssistantMessage) -> str:
    chunks: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            chunks.append(block.text)
    return "\n".join(chunks)


def _extract_token_usage(message: ResultMessage) -> TokenUsage | None:
    usage = getattr(message, "usage", None)
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    cost_usd = getattr(message, "total_cost_usd", None)
    if not input_tokens and not output_tokens:
        return None
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd)


async def _run_claude_prompt(
    prompt: str,
    cwd: Path,
    timeout_seconds: int,
    *,
    system_prompt: str | None = None,
    max_turns: int = 20,
    model: str | None = None,
    reasoning_effort: str | None = None,
    stderr_lines: list[str] | None = None,
) -> tuple[str, TokenUsage | None]:
    collector = stderr_lines if stderr_lines is not None else []
    options = ClaudeAgentOptions(
        cwd=cwd,
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        system_prompt=system_prompt,
        model=model,
        effort=reasoning_effort,
        stderr=lambda line: collector.append(line),
    )

    parts: list[str] = []
    final_result: str | None = None
    token_usage: TokenUsage | None = None

    with fail_after(timeout_seconds):
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                text = _collect_text_from_assistant(message)
                if text.strip():
                    parts.append(text)
            elif isinstance(message, ResultMessage):
                if message.result:
                    final_result = message.result
                token_usage = _extract_token_usage(message)

    merged = (final_result or "\n".join(parts)).strip()
    if not merged:
        raise RuntimeError("Claude returned an empty response")
    return merged, token_usage


def _build_local_review_prompt(pr: PRCandidate) -> str:
    if pr.review_mode == "uncommitted":
        diff_cmd = "git diff HEAD"
        extra = "Also run `git ls-files --others --exclude-standard` to find untracked new files.\n"
    else:
        diff_cmd = f"git diff {pr.base_ref}...{pr.head_sha}"
        extra = ""
    return (
        f"Review the code changes in this repository.\n"
        f"Run `{diff_cmd}` to see the diff.\n"
        f"{extra}"
        f"Context: {pr.title}\n"
        f"Base: {pr.base_ref}\n\n"
        "Focus only on actionable bugs, regressions, security issues, and missing tests. "
        "Return concise markdown with exactly:\n"
        "### Findings\n"
        "Severity: P0 = blocking / security vulnerability / data loss / breaks production, "
        "P1 = urgent logic error / correctness issue, "
        "P2 = non-trivial code quality issue / missing validation, "
        "P3 = minor style / nit.\n"
        "- [P0|P1|P2|P3] path[:line] - issue. Impact. Recommended fix.\n"
        "### Test Gaps\n"
        "- missing tests\n"
        "If no findings, write '- No material findings.' under Findings and '- None noted.' "
        "under Test Gaps."
    )


async def run_claude_review(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> ReviewerOutput:
    started = datetime.now(UTC)
    token_usage: TokenUsage | None = None
    stderr_lines: list[str] = []
    try:
        if pr.is_local:
            prompt = _build_local_review_prompt(pr)
        else:
            prompt = f"/review {pr.url}"
        markdown, token_usage = await _run_claude_prompt(
            prompt,
            workspace,
            timeout_seconds,
            model=model,
            reasoning_effort=reasoning_effort,
            stderr_lines=stderr_lines,
        )
        status = "ok"
        error = None
        stderr = ""
    except ProcessError as exc:
        markdown = ""
        status = "error"
        captured = "\n".join(stderr_lines).strip()
        stderr = captured or exc.stderr or ""
        error = f"exit_code={exc.exit_code} stderr={stderr}" if stderr else str(exc)
    except Exception as exc:  # noqa: BLE001
        markdown = ""
        status = "error"
        captured = "\n".join(stderr_lines).strip()
        stderr = captured or str(exc)
        error = str(exc)

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
        token_usage=token_usage,
    )
