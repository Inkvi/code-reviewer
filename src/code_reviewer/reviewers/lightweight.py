from __future__ import annotations

from pathlib import Path

from code_reviewer.logger import info
from code_reviewer.models import PRCandidate, TokenUsage
from code_reviewer.prompts import build_lightweight_bundle
from code_reviewer.reviewers._fallback import run_with_fallback
from code_reviewer.reviewers.claude_cli import run_claude_cli_prompt
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
    backend: list[str] | str = "claude",
    model: str | None = None,
    reasoning_effort: str | None = None,
    prompt_path: str | None = None,
    claude_backend: str = "sdk",
) -> tuple[str, TokenUsage | None]:
    backends = [backend] if isinstance(backend, str) else list(backend)
    bundle = build_lightweight_bundle(pr, workspace, prompt_path)
    prompt = bundle.prompt
    info(
        f"running lightweight review "
        f"(backends={' > '.join(backends)}, model={model or 'default'}) {pr.url}"
    )

    async def _try(b: str) -> tuple[str, TokenUsage | None]:
        is_primary = b == backends[0]
        use_model = model if is_primary else None
        use_effort = reasoning_effort if is_primary else None
        if b == "claude":
            if claude_backend == "cli":
                return await run_claude_cli_prompt(
                    prompt,
                    workspace,
                    timeout_seconds,
                    system_prompt=bundle.system_prompt,
                    max_turns=1,
                    model=use_model,
                    reasoning_effort=use_effort,
                )
            return await _run_claude_prompt(
                prompt,
                workspace,
                timeout_seconds,
                system_prompt=bundle.system_prompt,
                max_turns=1,
                model=use_model,
                reasoning_effort=use_effort,
            )
        if b == "codex":
            text = await run_codex_prompt(
                prompt,
                workspace,
                timeout_seconds,
                model=use_model,
                reasoning_effort=use_effort,
            )
            return text, None
        if b == "gemini":
            text = await run_gemini_prompt(
                prompt,
                workspace,
                timeout_seconds,
                model=use_model,
            )
            return text, None
        raise RuntimeError(f"Unsupported lightweight review backend: {b}")

    return await run_with_fallback(backends, _try, "lightweight", pr.url)
