from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from code_reviewer.models import PRCandidate, ReviewerOutput
from code_reviewer.prompts import build_full_review_bundle
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
        raise RuntimeError(f"codex prompt timed out after {timeout_seconds}s") from exc

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
    prompt_path: str | None = None,
) -> ReviewerOutput:
    started = datetime.now(UTC)

    try:
        bundle = build_full_review_bundle(pr, workspace, prompt_path)
        markdown = await run_codex_prompt(
            bundle.prompt,
            workspace,
            timeout_seconds,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        stdout = markdown
        stderr = ""
        status = "ok"
        error = None
    except TimeoutError:
        stdout = ""
        stderr = f"codex review timed out after {timeout_seconds}s"
        status = "error"
        error = stderr
        markdown = ""
    except Exception as exc:  # noqa: BLE001
        stdout = ""
        stderr = str(exc)
        status = "error"
        error = str(exc)
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
