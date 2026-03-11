from __future__ import annotations

from pathlib import Path

from code_reviewer.logger import info
from code_reviewer.models import PRCandidate, TokenUsage
from code_reviewer.prompts import build_lightweight_bundle
from code_reviewer.reviewers.claude_sdk import _run_claude_prompt
from code_reviewer.reviewers.codex_cli import run_codex_prompt
from code_reviewer.reviewers.gemini_cli import run_gemini_prompt


def _build_lightweight_prompt(pr: PRCandidate) -> str:
    return build_lightweight_bundle(pr, Path.cwd(), None).prompt


async def run_lightweight_review(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    backend: str = "claude",
    model: str | None = None,
    reasoning_effort: str | None = None,
    prompt_path: str | None = None,
) -> tuple[str, TokenUsage | None]:
    bundle = build_lightweight_bundle(pr, workspace, prompt_path)
    prompt = bundle.prompt
    info(f"running lightweight review (backend={backend}, model={model or 'default'}) {pr.url}")

    if backend == "claude":
        return await _run_claude_prompt(
            prompt,
            workspace,
            timeout_seconds,
            system_prompt=bundle.system_prompt,
            max_turns=1,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    if backend == "codex":
        text = await run_codex_prompt(
            prompt,
            workspace,
            timeout_seconds,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        return text, None
    if backend == "gemini":
        text = await run_gemini_prompt(
            prompt,
            workspace,
            timeout_seconds,
            model=model,
        )
        return text, None
    raise RuntimeError(f"Unsupported lightweight review backend: {backend}")
