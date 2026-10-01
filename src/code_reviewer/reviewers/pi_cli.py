from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from code_reviewer.models import PRCandidate, ReviewerOutput
from code_reviewer.prompts import build_full_review_bundle
from code_reviewer.shell import run_command_async


def _build_pi_command(prompt: str, *, model: str | None = None) -> list[str]:
    # --no-session keeps each run from writing a session file under ~/.pi.
    args = ["pi", "-p", "--mode", "json", "--no-session"]
    if model:
        args.extend(["--model", model])
    args.append(prompt)
    return args


def _parse_pi_events(stdout: str) -> list[dict]:
    """Parse pi --mode json output into a list of events."""
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


def _last_assistant_message(events: list[dict]) -> dict | None:
    for event in reversed(events):
        if event.get("type") != "message_end":
            continue
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            return message
    return None


def _extract_pi_text(message: dict) -> str:
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n".join(parts).strip()


async def run_pi_prompt(
    prompt: str,
    cwd: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
) -> tuple[str, list[dict] | None]:
    args = _build_pi_command(prompt, model=model)
    try:
        code, stdout, stderr = await run_command_async(
            args,
            cwd=cwd,
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise RuntimeError(f"pi prompt timed out after {timeout_seconds}s") from exc

    events = _parse_pi_events(stdout)
    # message_update events repeat the whole message on every token, so leave them out.
    conversation = [e for e in events if e.get("type") != "message_update"] or None
    message = _last_assistant_message(events)

    if code != 0:
        detail = stderr.strip()
        if not detail:
            detail = stdout.strip()[:500] or "(no output)"
        raise RuntimeError(f"pi exited with status {code}: {detail}")
    # pi exits 0 when the model API call fails; the error is on the last assistant message.
    if message is not None and message.get("stopReason") in ("error", "aborted"):
        detail = message.get("errorMessage") or message.get("stopReason")
        raise RuntimeError(f"pi failed: {detail}")
    markdown = _extract_pi_text(message) if message is not None else ""
    if not markdown:
        raise RuntimeError("pi returned an empty response")
    return markdown, conversation


async def run_pi_review(
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
        markdown, conversation = await run_pi_prompt(
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
        stderr = f"pi review timed out after {timeout_seconds}s"
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
        reviewer="pi",
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
