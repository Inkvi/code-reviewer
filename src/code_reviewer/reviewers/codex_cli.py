from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from code_reviewer.models import PRCandidate, ReviewerOutput
from code_reviewer.shell import run_command_async


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


def _extract_codex_markdown_from_jsonl(stdout: str) -> tuple[str, int]:
    event_count = 0
    last_agent_message = ""

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(payload, dict):
            continue

        event_count += 1
        if payload.get("type") != "item.completed":
            continue
        item = payload.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            last_agent_message = text.strip()

    return _sanitize_codex_markdown(last_agent_message), event_count


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
    json_mode: bool,
) -> list[str]:
    args = ["codex", "review"]
    if pr.is_local and pr.review_mode == "uncommitted":
        args.append("--uncommitted")
    else:
        base_ref = pr.base_ref if pr.is_local else f"origin/{pr.base_ref}"
        args.extend(["--base", base_ref])
    if json_mode:
        args.append("--json")
    if model:
        args.extend(["-c", f'model="{model}"'])
    if reasoning_effort:
        args.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    return args


def _build_codex_exec_command(
    prompt: str,
    *,
    model: str | None,
    reasoning_effort: str | None,
    output_last_message_path: Path,
) -> list[str]:
    args = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--output-last-message",
        str(output_last_message_path),
    ]
    if model:
        args.extend(["-m", model])
    if reasoning_effort:
        args.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    args.append(prompt)
    return args


def _codex_review_json_unsupported(stderr: str) -> bool:
    lowered = stderr.lower()
    return "unexpected argument '--json'" in lowered or 'unexpected argument "--json"' in lowered


async def run_codex_prompt(
    prompt: str,
    workspace: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    output_last_message_path = workspace / f".codex-last-message-{uuid4().hex}.md"
    try:
        code, raw_stdout, stderr = await run_command_async(
            _build_codex_exec_command(
                prompt,
                model=model,
                reasoning_effort=reasoning_effort,
                output_last_message_path=output_last_message_path,
            ),
            cwd=workspace,
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise RuntimeError(f"codex reconciliation timed out after {timeout_seconds}s") from exc

    output_last_message = ""
    if output_last_message_path.exists():
        output_last_message = output_last_message_path.read_text(encoding="utf-8", errors="replace")
        output_last_message_path.unlink(missing_ok=True)

    markdown = _sanitize_codex_markdown(output_last_message.strip())
    if not markdown:
        markdown = _extract_codex_review_text(raw_stdout, stderr)
    if code != 0:
        raise RuntimeError(f"codex exited with status {code}: {stderr.strip()}")
    if not markdown:
        raise RuntimeError("Codex returned an empty response")
    return markdown


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
        json_mode_used = True
        json_fallback_used = False
        code, raw_stdout, stderr = await run_command_async(
            _build_codex_review_command(
                pr,
                model=model,
                reasoning_effort=reasoning_effort,
                json_mode=True,
            ),
            cwd=workspace,
            timeout=timeout_seconds,
        )
        if code != 0 and _codex_review_json_unsupported(stderr):
            code, raw_stdout, stderr = await run_command_async(
                _build_codex_review_command(
                    pr,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    json_mode=False,
                ),
                cwd=workspace,
                timeout=timeout_seconds,
            )
            json_mode_used = False
            json_fallback_used = True

        status = "ok" if code == 0 else "error"
        error = None if code == 0 else f"codex exited with status {code}: {stderr.strip()}"
        markdown = ""
        event_count = 0
        if json_mode_used:
            markdown, event_count = _extract_codex_markdown_from_jsonl(raw_stdout)
        if not markdown:
            markdown = _extract_codex_review_text(raw_stdout, stderr)
        if json_mode_used and event_count > 0:
            stdout = f"codex JSON events captured: {event_count}"
        elif json_mode_used:
            stdout = "codex JSON mode enabled but no parseable events were captured"
        elif json_fallback_used:
            stdout = "codex review JSON mode unsupported; used plain review output"
        else:
            stdout = raw_stdout
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
