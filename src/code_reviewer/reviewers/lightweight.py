from __future__ import annotations

import logging
from pathlib import Path

from code_reviewer.logger import info
from code_reviewer.models import PRCandidate, TokenUsage
from code_reviewer.prompts import PromptBundle, build_lightweight_bundle
from code_reviewer.reviewers._circuit_breaker import is_open as _cb_is_open
from code_reviewer.reviewers._circuit_breaker import record_failure as _cb_record_failure
from code_reviewer.reviewers._fallback import run_with_fallback
from code_reviewer.reviewers._sanitize import _escape_delimiters
from code_reviewer.reviewers.antigravity_cli import run_antigravity_prompt
from code_reviewer.reviewers.claude_cli import run_claude_cli_prompt
from code_reviewer.reviewers.claude_sdk import _run_claude_prompt
from code_reviewer.reviewers.codex_cli import run_codex_prompt
from code_reviewer.reviewers.opencode_cli import run_opencode_prompt
from code_reviewer.reviewers.triage import _get_diff_snippet

log = logging.getLogger(__name__)


def _build_diff_section(workspace: Path, pr: PRCandidate) -> str:
    diff_snippet = _get_diff_snippet(workspace, pr)
    return _escape_delimiters(diff_snippet) if diff_snippet else ""


def _build_lightweight_prompt(pr: PRCandidate) -> str:
    return build_lightweight_bundle(pr, Path.cwd(), "", None).prompt


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
    antigravity_fallback_model: str | None = None,
) -> tuple[str, TokenUsage | None, PromptBundle]:
    backends = [backend] if isinstance(backend, str) else list(backend)
    diff_section = _build_diff_section(workspace, pr)
    bundle = build_lightweight_bundle(pr, workspace, diff_section, prompt_path)
    prompt = bundle.prompt
    info(
        f"running lightweight review "
        f"(backends={' > '.join(backends)}, model={model or 'default'}) {pr.url}"
    )

    # Resolve effective antigravity model: if primary's circuit is open, use fallback
    _base_antigravity_model = model if backends[0] == "antigravity" else None
    _effective_antigravity_model = _base_antigravity_model
    if antigravity_fallback_model and antigravity_fallback_model != _base_antigravity_model:
        opened, _ = _cb_is_open("antigravity", _base_antigravity_model)
        if opened:
            _effective_antigravity_model = antigravity_fallback_model

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
            text, usage, _ = await _run_claude_prompt(
                prompt,
                workspace,
                timeout_seconds,
                system_prompt=bundle.system_prompt,
                max_turns=1,
                model=use_model,
                reasoning_effort=use_effort,
            )
            return text, usage
        if b == "codex":
            text, _ = await run_codex_prompt(
                prompt,
                workspace,
                timeout_seconds,
                model=use_model,
                reasoning_effort=use_effort,
            )
            return text, None
        if b == "antigravity":
            use_model = _effective_antigravity_model
            try:
                text = await run_antigravity_prompt(
                    prompt,
                    workspace,
                    timeout_seconds,
                    model=use_model,
                )
                return text, None
            except RuntimeError as exc:
                if (
                    antigravity_fallback_model
                    and use_model != antigravity_fallback_model
                    and "reset after" in str(exc)
                ):
                    _cb_record_failure("antigravity", use_model, exc)
                    fb_opened, _ = _cb_is_open("antigravity", antigravity_fallback_model)
                    if not fb_opened:
                        log.info(
                            "retrying antigravity lightweight review with fallback model %s %s",
                            antigravity_fallback_model,
                            pr.url,
                        )
                        text = await run_antigravity_prompt(
                            prompt,
                            workspace,
                            timeout_seconds,
                            model=antigravity_fallback_model,
                        )
                        return text, None
                raise
        if b == "opencode":
            text, _ = await run_opencode_prompt(
                prompt,
                workspace,
                timeout_seconds,
                model=use_model,
            )
            return text, None
        raise RuntimeError(f"Unsupported lightweight review backend: {b}")

    models_map: dict[str, str | None] = {}
    for b in backends:
        models_map[b] = (
            _effective_antigravity_model
            if b == "antigravity"
            else (model if b == backends[0] else None)
        )
    text, usage = await run_with_fallback(backends, _try, "lightweight", pr.url, models=models_map)
    return text, usage, bundle
