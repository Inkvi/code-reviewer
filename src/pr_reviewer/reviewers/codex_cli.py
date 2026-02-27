from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pr_reviewer.models import PRCandidate, ReviewerOutput
from pr_reviewer.shell import run_command_async


def _extract_codex_review_text(stdout: str, stderr: str) -> str:
    stdout_text = stdout.strip()
    if stdout_text:
        return stdout_text

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


async def run_codex_review(
    pr: PRCandidate, workspace: Path, timeout_seconds: int
) -> ReviewerOutput:
    started = datetime.now(UTC)
    output_file = (workspace / f".codex-review-last-message-{uuid4().hex}.md").resolve()

    try:
        code, stdout, stderr = await run_command_async(
            [
                "codex",
                "exec",
                "review",
                "--base",
                f"origin/{pr.base_ref}",
                "--output-last-message",
                str(output_file),
            ],
            cwd=workspace,
            timeout=timeout_seconds,
        )
        status = "ok" if code == 0 else "error"
        error = None if code == 0 else f"codex exited with status {code}: {stderr.strip()}"
        markdown = (
            output_file.read_text(encoding="utf-8").strip() if output_file.exists() else ""
        )
        if not markdown:
            markdown = _extract_codex_review_text(stdout, stderr)
        else:
            markdown = _sanitize_codex_markdown(markdown)
    except TimeoutError:
        stdout = ""
        stderr = f"codex review timed out after {timeout_seconds}s"
        status = "error"
        error = stderr
        markdown = ""
    finally:
        output_file.unlink(missing_ok=True)

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
