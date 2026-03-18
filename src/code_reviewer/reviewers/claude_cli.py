from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from code_reviewer.models import PRCandidate, ReviewerOutput
from code_reviewer.prompts import build_full_review_bundle
from code_reviewer.shell import run_command_async


def _build_claude_cli_command(
    prompt: str,
    *,
    model: str | None = None,
    system_prompt: str | None = None,
    max_turns: int | None = None,
    reasoning_effort: str | None = None,
) -> list[str]:
    args = ["claude", "-p", prompt, "--output-format", "text", "--dangerously-skip-permissions"]
    if model:
        args.extend(["--model", model])
    if system_prompt:
        args.extend(["--system-prompt", system_prompt])
    if max_turns is not None:
        args.extend(["--max-turns", str(max_turns)])
    if reasoning_effort:
        args.extend(["--effort", reasoning_effort])
    return args


async def run_claude_cli_prompt(
    prompt: str,
    cwd: Path,
    timeout_seconds: int,
    *,
    system_prompt: str | None = None,
    max_turns: int | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> tuple[str, None]:
    args = _build_claude_cli_command(
        prompt,
        model=model,
        system_prompt=system_prompt,
        max_turns=max_turns,
        reasoning_effort=reasoning_effort,
    )

    try:
        code, stdout, stderr = await run_command_async(
            args,
            cwd=cwd,
            timeout=timeout_seconds,
            env={"CLAUDECODE": ""},
        )
    except TimeoutError as exc:
        raise RuntimeError(f"claude CLI timed out after {timeout_seconds}s") from exc

    if code != 0:
        raise RuntimeError(f"claude CLI exited with status {code}: {stderr.strip()}")

    text = stdout.strip()
    if not text:
        raise RuntimeError("Claude CLI returned an empty response")

    return text, None


async def run_claude_cli_review(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    prompt_path: str | None = None,
) -> ReviewerOutput:
    started = datetime.now(UTC)
    prompt_text = ""
    system_prompt_text: str | None = None

    try:
        bundle = build_full_review_bundle(pr, workspace, prompt_path)
        prompt_text = bundle.prompt
        system_prompt_text = bundle.system_prompt
        text, _ = await run_claude_cli_prompt(
            bundle.prompt,
            workspace,
            timeout_seconds,
            system_prompt=bundle.system_prompt,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        markdown = text
        stdout = text
        stderr = ""
        status = "ok"
        error = None
    except TimeoutError:
        stdout = ""
        stderr = f"claude CLI review timed out after {timeout_seconds}s"
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
        reviewer="claude",
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
