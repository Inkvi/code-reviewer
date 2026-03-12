from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from code_reviewer.models import PRCandidate, ReviewerOutput
from code_reviewer.prompts import build_full_review_bundle
from code_reviewer.shell import run_command_async

_CODE_REVIEW_PROMPT = "/code-review"
_CODE_REVIEW_EXTENSION = "code-review"


def _build_gemini_review_command(
    _pr: PRCandidate,
    *,
    model: str | None,
) -> list[str]:
    args = [
        "gemini",
        "-p",
        _CODE_REVIEW_PROMPT,
        "-e",
        _CODE_REVIEW_EXTENSION,
        "--approval-mode",
        "yolo",
        "--output-format",
        "json",
    ]
    if model:
        args.extend(["-m", model])
    return args


def _build_gemini_prompt_command(prompt: str, *, model: str | None) -> list[str]:
    args = [
        "gemini",
        "-p",
        prompt,
        "--approval-mode",
        "yolo",
        "--output-format",
        "json",
    ]
    if model:
        args.extend(["-m", model])
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


def _extract_gemini_markdown_from_json(stdout: str) -> str:
    """Try to extract review markdown from JSON output."""
    payloads = _iter_json_payloads(stdout)
    for payload in reversed(payloads):
        markdown = _extract_markdown_from_payload(payload)
        if markdown:
            return markdown

    return ""


def _summarize_gemini_error(stderr: str) -> str:
    """Extract a concise error summary from gemini's verbose stderr.

    Gemini CLI dumps full JS stack traces on failure. Pull out just the
    meaningful error class/message (e.g. TerminalQuotaError line) and
    drop the noise. The full stderr is still available in ReviewerOutput.stderr.
    """
    lines = stderr.strip().splitlines()
    # Look for a recognizable "ErrorType: message" line
    for line in lines:
        stripped = line.strip()
        if "Error:" in stripped and not stripped.startswith("at "):
            return stripped
    # Fallback: first non-empty line, capped
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return stderr.strip()[:200]


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


async def run_gemini_review(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
    prompt_path: str | None = None,
) -> ReviewerOutput:
    started = datetime.now(UTC)

    try:
        if prompt_path is None:
            args = _build_gemini_review_command(pr, model=model)
            code, raw_stdout, stderr = await run_command_async(
                args,
                cwd=workspace,
                timeout=timeout_seconds,
            )
            status = "ok" if code == 0 else "error"
            error = (
                None
                if code == 0
                else f"gemini exited with status {code}: {_summarize_gemini_error(stderr)}"
            )
            markdown = _extract_gemini_review_text(raw_stdout, stderr)
            stdout = raw_stdout
        else:
            bundle = build_full_review_bundle(pr, workspace, prompt_path)
            markdown = await run_gemini_prompt(
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
        stderr = f"gemini review timed out after {timeout_seconds}s"
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
        reviewer="gemini",
        status=status,
        markdown=markdown,
        stdout=stdout,
        stderr=stderr,
        error=error,
        started_at=started,
        ended_at=ended,
    )


async def run_gemini_prompt(
    prompt: str,
    workspace: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
) -> str:
    try:
        code, raw_stdout, stderr = await run_command_async(
            _build_gemini_prompt_command(prompt, model=model),
            cwd=workspace,
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise RuntimeError(f"gemini prompt timed out after {timeout_seconds}s") from exc

    if code != 0:
        raise RuntimeError(f"gemini exited with status {code}: {_summarize_gemini_error(stderr)}")

    markdown = _extract_gemini_review_text(raw_stdout, stderr)
    if not markdown:
        raise RuntimeError("Gemini returned an empty response")
    return markdown
