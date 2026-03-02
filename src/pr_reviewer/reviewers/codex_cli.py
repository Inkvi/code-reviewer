from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pr_reviewer.models import PRCandidate, ReviewerOutput
from pr_reviewer.shell import run_command_async


def _extract_codex_review_text(stdout: str, stderr: str) -> str:
    stdout_text = stdout.strip()
    if stdout_text:
        return _sanitize_codex_markdown(stdout_text)

    lines = stderr.splitlines()
    if not lines:
        return ""

    # In some Codex CLI versions, the final review body is emitted on stderr as:
    #   codex
    #   <final review text...>
    for marker in ("codex", "assistant"):
        indices = [i for i, line in enumerate(lines) if line.strip() == marker]
        if indices:
            start = indices[-1] + 1
            candidate = "\n".join(lines[start:]).strip()
            if candidate:
                return _sanitize_codex_markdown(candidate)

    return _sanitize_codex_markdown("")


def _sanitize_codex_markdown(text: str) -> str:
    if not text:
        return ""

    skip_prefixes = (
        "Failed to write last message file ",
        "Warning: no last agent message; wrote empty content to ",
    )
    lines = []
    for line in text.splitlines():
        if line.startswith(skip_prefixes):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _build_codex_review_command(
    pr: PRCandidate,
    *,
    model: str | None,
    reasoning_effort: str | None,
) -> list[str]:
    args = [
        "codex",
        "review",
        "--base",
        f"origin/{pr.base_ref}",
    ]
    if model:
        args.extend(["-c", f'model="{model}"'])
    if reasoning_effort:
        args.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    return args


async def run_codex_review(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> ReviewerOutput:
    started = datetime.now(UTC)

    try:
        code, stdout, stderr = await run_command_async(
            _build_codex_review_command(
                pr,
                model=model,
                reasoning_effort=reasoning_effort,
            ),
            cwd=workspace,
            timeout=timeout_seconds,
        )
        status = "ok" if code == 0 else "error"
        error = None if code == 0 else f"codex exited with status {code}: {stderr.strip()}"
        markdown = _extract_codex_review_text(stdout, stderr)
    except TimeoutError:
        stdout = ""
        stderr = f"codex review timed out after {timeout_seconds}s"
        status = "error"
        error = stderr
        markdown = ""

    ended = datetime.now(UTC)
    return ReviewerOutput(
        reviewer="codex",
        status=status,
        markdown=markdown,
        stdout=stdout,
        stderr=stderr,
        error=error,
        started_at=started,
        ended_at=ended,
    )
