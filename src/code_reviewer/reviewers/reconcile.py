from __future__ import annotations

import logging
from pathlib import Path

from code_reviewer.models import PRCandidate, ReviewerOutput, TokenUsage
from code_reviewer.prompts import PromptBundle, build_reconcile_bundle
from code_reviewer.reviewers._circuit_breaker import is_open as _cb_is_open
from code_reviewer.reviewers._circuit_breaker import record_failure as _cb_record_failure
from code_reviewer.reviewers._fallback import run_with_fallback
from code_reviewer.reviewers.claude_cli import run_claude_cli_prompt
from code_reviewer.reviewers.claude_sdk import _run_claude_prompt
from code_reviewer.reviewers.codex_cli import run_codex_prompt
from code_reviewer.reviewers.gemini_cli import run_gemini_prompt
from code_reviewer.reviewers.opencode_cli import run_opencode_prompt

log = logging.getLogger(__name__)


async def reconcile_reviews(
    pr: PRCandidate,
    workspace: Path,
    reviewer_outputs: list[ReviewerOutput],
    timeout_seconds: int | dict[str, int],
    *,
    reconciler_backend: list[str] | str = "claude",
    reconciler_model: str | None = None,
    reconciler_reasoning_effort: str | None = None,
    max_findings: int = 10,
    max_test_gaps: int = 3,
    prompt_path: str | None = None,
    claude_backend: str = "sdk",
    gemini_fallback_model: str | None = None,
) -> tuple[str, TokenUsage | None, PromptBundle]:
    backends = (
        [reconciler_backend] if isinstance(reconciler_backend, str) else list(reconciler_backend)
    )
    bundle = build_reconcile_bundle(
        pr,
        workspace,
        reviewer_outputs,
        max_findings,
        max_test_gaps,
        prompt_path,
    )
    prompt = bundle.prompt

    # Resolve effective gemini model: if primary's circuit is open, use fallback
    _base_gemini_model = reconciler_model if backends[0] == "gemini" else None
    _effective_gemini_model = _base_gemini_model
    if gemini_fallback_model and gemini_fallback_model != _base_gemini_model:
        opened, _ = _cb_is_open("gemini", _base_gemini_model)
        if opened:
            _effective_gemini_model = gemini_fallback_model

    def _timeout_for(b: str) -> int:
        if isinstance(timeout_seconds, dict):
            return timeout_seconds.get(b, next(iter(timeout_seconds.values())))
        return timeout_seconds

    async def _try(b: str) -> tuple[str, TokenUsage | None]:
        is_primary = b == backends[0]
        use_model = reconciler_model if is_primary else None
        use_effort = reconciler_reasoning_effort if is_primary else None
        t = _timeout_for(b)
        if b == "claude":
            if claude_backend == "cli":
                return await run_claude_cli_prompt(
                    prompt,
                    workspace,
                    t,
                    system_prompt=bundle.system_prompt,
                    max_turns=1,
                    model=use_model,
                    reasoning_effort=use_effort,
                )
            text, usage, _ = await _run_claude_prompt(
                prompt,
                workspace,
                t,
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
                t,
                model=use_model,
                reasoning_effort=use_effort,
            )
            return text, None
        if b == "gemini":
            use_model = _effective_gemini_model
            try:
                text = await run_gemini_prompt(
                    prompt,
                    workspace,
                    t,
                    model=use_model,
                )
                return text, None
            except RuntimeError as exc:
                if (
                    gemini_fallback_model
                    and use_model != gemini_fallback_model
                    and "reset after" in str(exc)
                ):
                    _cb_record_failure("gemini", use_model, exc)
                    fb_opened, _ = _cb_is_open("gemini", gemini_fallback_model)
                    if not fb_opened:
                        log.info(
                            "retrying gemini reconcile with fallback model %s %s",
                            gemini_fallback_model,
                            pr.url,
                        )
                        text = await run_gemini_prompt(
                            prompt,
                            workspace,
                            t,
                            model=gemini_fallback_model,
                        )
                        return text, None
                raise
        if b == "opencode":
            text, _ = await run_opencode_prompt(
                prompt,
                workspace,
                t,
                model=use_model,
            )
            return text, None
        raise RuntimeError(f"Unsupported reconciler backend: {b}")

    models_map: dict[str, str | None] = {}
    for b in backends:
        models_map[b] = (
            _effective_gemini_model
            if b == "gemini"
            else (reconciler_model if b == backends[0] else None)
        )
    text, usage = await run_with_fallback(backends, _try, "reconcile", pr.url, models=models_map)
    return text, usage, bundle
