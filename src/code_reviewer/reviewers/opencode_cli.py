from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from code_reviewer.models import PRCandidate, ReviewerOutput
from code_reviewer.prompts import build_full_review_bundle
from code_reviewer.shell import run_command_async


def _extract_opencode_text(stdout: str) -> str:
    """Extract concatenated text from OpenCode JSONL output."""
    parts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") != "text":
            continue
        part = event.get("part")
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n".join(parts)


def _build_opencode_command(prompt: str, *, model: str | None = None) -> list[str]:
    args = ["opencode", "run", "--format", "json"]
    if model:
        args.extend(["-m", model])
    args.append(prompt)
    return args


def _parse_opencode_events(stdout: str) -> list[dict]:
    """Parse OpenCode JSONL output into a list of events."""
    events: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except json.JSONDecodeError:
            continue
    return events


async def run_opencode_prompt(
    prompt: str,
    cwd: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
) -> tuple[str, list[dict] | None]:
    args = _build_opencode_command(prompt, model=model)
    try:
        code, stdout, stderr = await run_command_async(
            args,
            cwd=cwd,
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise RuntimeError(f"opencode prompt timed out after {timeout_seconds}s") from exc

    events = _parse_opencode_events(stdout)
    conversation = events or None
    markdown = _extract_opencode_text(stdout)

    if code != 0:
        detail = stderr.strip()
        if not detail:
            detail = stdout.strip()[:500] or "(no output)"
        raise RuntimeError(f"opencode exited with status {code}: {detail}")
    if not markdown:
        raise RuntimeError("OpenCode returned an empty response")
    return markdown, conversation


async def run_opencode_review(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
    prompt_path: str | None = None,
) -> ReviewerOutput:
    started = datetime.now(UTC)
    prompt_text = ""
    system_prompt_text: str | None = None

    try:
        bundle = build_full_review_bundle(pr, workspace, prompt_path)
        prompt_text = bundle.prompt
        system_prompt_text = bundle.system_prompt
        markdown, conversation = await run_opencode_prompt(
            bundle.prompt,
            workspace,
            timeout_seconds,
            model=model,
        )
        stdout = markdown
        stderr = ""
        status = "ok"
        error = None
    except TimeoutError:
        stdout = ""
        stderr = f"opencode review timed out after {timeout_seconds}s"
        status = "error"
        error = stderr
        markdown = ""
        conversation = None
    except Exception as exc:  # noqa: BLE001
        stdout = ""
        stderr = str(exc)
        status = "error"
        error = str(exc)
        markdown = ""
        conversation = None

    ended = datetime.now(UTC)
    return ReviewerOutput(
        reviewer="opencode",
        status=status,
        markdown=markdown,
        stdout=stdout,
        stderr=stderr,
        error=error,
        started_at=started,
        ended_at=ended,
        prompt=prompt_text,
        system_prompt=system_prompt_text,
        conversation=conversation,
    )
