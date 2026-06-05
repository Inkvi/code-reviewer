from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from code_reviewer.models import PRCandidate, ReviewerOutput
from code_reviewer.prompts import build_full_review_bundle
from code_reviewer.shell import run_command_async


def _build_antigravity_prompt_command(
    prompt: str,
    *,
    model: str | None,
    timeout_seconds: int,
) -> list[str]:
    # agy prints plain text (no JSON output mode); --print-timeout defaults to
    # 5m which is shorter than review timeouts, so pass ours explicitly.
    args = [
        "agy",
        "-p",
        prompt,
        "--dangerously-skip-permissions",
        "--print-timeout",
        f"{timeout_seconds}s",
    ]
    if model:
        args.extend(["--model", model])
    return args


def _extract_markdown_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""

    for key in ("response", "text", "output", "result", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    parts = payload.get("parts")
    if isinstance(parts, list):
        text_parts: list[str] = []
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())
        if text_parts:
            return "\n".join(text_parts)

    return ""


def _iter_json_payloads(text: str) -> list[object]:
    payloads: list[object] = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start == -1:
            break
        try:
            payload, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        payloads.append(payload)
        index = end
    return payloads


def _extract_antigravity_markdown_from_json(stdout: str) -> str:
    """Try to extract review markdown from JSON output."""
    payloads = _iter_json_payloads(stdout)
    for payload in reversed(payloads):
        markdown = _extract_markdown_from_payload(payload)
        if markdown:
            return markdown

    return ""


def _summarize_antigravity_error(stderr: str) -> str:
    """Extract a concise error summary from agy's stderr.

    Pull out a recognizable "Error: message" line and drop the noise.
    The full stderr is still available in ReviewerOutput.stderr.
    """
    lines = stderr.strip().splitlines()
    for line in lines:
        stripped = line.strip()
        if "Error:" in stripped and not stripped.startswith("at "):
            return stripped
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return stderr.strip()[:200]


def _extract_antigravity_review_text(stdout: str) -> str:
    """Extract review text from agy output, trying JSON then plain text."""
    markdown = _extract_antigravity_markdown_from_json(stdout)
    if markdown:
        return markdown

    stdout_text = stdout.strip()
    if stdout_text:
        return stdout_text

    return ""


async def run_antigravity_review(
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
        if prompt_path is None:
            raise RuntimeError(
                "antigravity reviewer requires full_review_prompt_path "
                "(prompt mode is the only supported mode)"
            )
        bundle = build_full_review_bundle(pr, workspace, prompt_path)
        prompt_text = bundle.prompt
        system_prompt_text = bundle.system_prompt
        markdown = await run_antigravity_prompt(
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
        stderr = f"agy review timed out after {timeout_seconds}s"
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
        reviewer="antigravity",
        status=status,
        markdown=markdown,
        stdout=stdout,
        stderr=stderr,
        error=error,
        started_at=started,
        ended_at=ended,
        prompt=prompt_text,
        system_prompt=system_prompt_text,
    )


async def run_antigravity_prompt(
    prompt: str,
    workspace: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
) -> str:
    try:
        code, raw_stdout, stderr = await run_command_async(
            _build_antigravity_prompt_command(
                prompt, model=model, timeout_seconds=timeout_seconds
            ),
            cwd=workspace,
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise RuntimeError(f"agy prompt timed out after {timeout_seconds}s") from exc

    if code != 0:
        detail = _summarize_antigravity_error(stderr)
        if not detail:
            detail = raw_stdout.strip()[:500] or "(no output)"
        raise RuntimeError(f"agy exited with status {code}: {detail}")

    markdown = _extract_antigravity_review_text(raw_stdout)
    if not markdown:
        raise RuntimeError("Antigravity returned an empty response")
    return markdown
