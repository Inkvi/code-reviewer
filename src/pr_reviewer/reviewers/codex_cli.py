from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pr_reviewer.models import PRCandidate, ReviewerOutput
from pr_reviewer.shell import run_command_async


async def run_codex_review(
    pr: PRCandidate, workspace: Path, timeout_seconds: int
) -> ReviewerOutput:
    started = datetime.now(UTC)

    try:
        prompt = (
            "Review this pull request and return markdown findings ordered by severity. "
            "Focus on correctness, regressions, and missing tests."
        )
        code, stdout, stderr = await run_command_async(
            ["codex", "review", "--base", f"origin/{pr.base_ref}", prompt],
            cwd=workspace,
            timeout=timeout_seconds,
        )
        status = "ok" if code == 0 else "error"
        error = None if code == 0 else f"codex exited with status {code}"
        markdown = stdout.strip()
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
