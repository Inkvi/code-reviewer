from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pr_reviewer.models import PRCandidate, ReviewerOutput
from pr_reviewer.shell import run_command_async

_REVIEW_PROMPT_TEMPLATE = """\
Review the code changes in this repository.

Pull request: {url}
Title: {title}
Base branch: origin/{base_ref}
Head SHA: {head_sha}

Instructions:
1. Run `git diff origin/{base_ref}...HEAD` to see the diff.
2. Read the full content of each changed file for context.
3. Focus only on actionable bugs, regressions, and missing tests.
4. Return concise markdown with exactly these two sections:

### Findings
- [P1|P2|P3] path[:line] - issue. Impact. Recommended fix.

### Test Gaps
- missing tests

If no findings, write '- No material findings.' under Findings.
If no test gaps, write '- None noted.' under Test Gaps.

Keep total output under 220 words. No tables, no long summary, no praise/filler.\
"""


def _build_gemini_review_command(
    pr: PRCandidate,
    *,
    model: str | None,
) -> list[str]:
    prompt = _REVIEW_PROMPT_TEMPLATE.format(
        url=pr.url,
        title=pr.title,
        base_ref=pr.base_ref,
        head_sha=pr.head_sha,
    )
    args = ["gemini", "-p", prompt]
    if model:
        args.extend(["-m", model])
    return args


def _extract_gemini_markdown_from_json(stdout: str) -> str:
    """Try to extract review markdown from JSON output."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue

        if isinstance(payload, dict):
            for key in ("text", "output", "result", "content", "response"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

            parts = payload.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict):
                        text = part.get("text")
                        if isinstance(text, str) and text.strip():
                            return text.strip()

    return ""


def _extract_gemini_review_text(stdout: str, stderr: str) -> str:
    """Extract review text from gemini CLI output, trying JSON then plain text."""
    markdown = _extract_gemini_markdown_from_json(stdout)
    if markdown:
        return markdown

    stdout_text = stdout.strip()
    if stdout_text:
        return stdout_text

    lines = stderr.splitlines()
    if not lines:
        return ""

    for marker in ("gemini", "assistant", "model"):
        indices = [i for i, line in enumerate(lines) if line.strip() == marker]
        if indices:
            start = indices[-1] + 1
            candidate = "\n".join(lines[start:]).strip()
            if candidate:
                return candidate

    return ""


def _gemini_json_unsupported(stderr: str) -> bool:
    lowered = stderr.lower()
    return (
        "unknown option" in lowered
        or "unexpected argument" in lowered
        or "unrecognized" in lowered
    ) and "output-format" in lowered


async def run_gemini_review(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
) -> ReviewerOutput:
    started = datetime.now(UTC)

    try:
        args = _build_gemini_review_command(pr, model=model)
        code, raw_stdout, stderr = await run_command_async(
            args,
            cwd=workspace,
            timeout=timeout_seconds,
        )

        status = "ok" if code == 0 else "error"
        error = None if code == 0 else f"gemini exited with status {code}: {stderr.strip()}"
        markdown = _extract_gemini_review_text(raw_stdout, stderr)
        stdout = raw_stdout
    except TimeoutError:
        stdout = ""
        stderr = f"gemini review timed out after {timeout_seconds}s"
        status = "error"
        error = stderr
        markdown = ""

    ended = datetime.now(UTC)
    return ReviewerOutput(
        reviewer="gemini",
        status=status,
        markdown=markdown,
        stdout=stdout,
        stderr=stderr,
        error=error,
        started_at=started,
        ended_at=ended,
    )
