from __future__ import annotations

from pathlib import Path

from code_reviewer.models import PRCandidate, ReviewerOutput, TokenUsage
from code_reviewer.prompts import build_reconcile_bundle
from code_reviewer.reviewers._fallback import run_with_fallback
from code_reviewer.reviewers._sanitize import _escape_delimiters
from code_reviewer.reviewers.claude_cli import run_claude_cli_prompt
from code_reviewer.reviewers.claude_sdk import _run_claude_prompt
from code_reviewer.reviewers.codex_cli import run_codex_prompt
from code_reviewer.reviewers.gemini_cli import run_gemini_prompt

_SUSPICIOUS_PATTERNS = (
    "ignore previous",
    "ignore above",
    "ignore all",
    "disregard",
    "new instructions",
    "system prompt",
    "you are now",
    "act as",
    "forget your",
    "override",
    "do not report",
    "no findings",
    "output exactly",
)


def _sanitize_comment(text: str) -> str:
    lowered = text.lower()
    for pattern in _SUSPICIOUS_PATTERNS:
        if pattern in lowered:
            return "[comment filtered: suspicious content]"
    return _escape_delimiters(text)


def _format_pr_comments(pr_comments: list[str] | None) -> str:
    if not pr_comments:
        return "_None provided._"
    sections = [f"- {_sanitize_comment(entry)}" for entry in pr_comments]
    return "\n".join(sections)


async def reconcile_reviews(
    pr: PRCandidate,
    workspace: Path,
    reviewer_outputs: list[ReviewerOutput],
    timeout_seconds: int | dict[str, int],
    *,
    reconciler_backend: list[str] | str = "claude",
    pr_comments: list[str] | None = None,
    reconciler_model: str | None = None,
    reconciler_reasoning_effort: str | None = None,
    max_findings: int = 10,
    max_test_gaps: int = 3,
    prompt_path: str | None = None,
    claude_backend: str = "sdk",
) -> tuple[str, TokenUsage | None]:
    backends = (
        [reconciler_backend] if isinstance(reconciler_backend, str) else list(reconciler_backend)
    )
    comments_text = _format_pr_comments(pr_comments)
    bundle = build_reconcile_bundle(
        pr,
        workspace,
        reviewer_outputs,
        comments_text,
        max_findings,
        max_test_gaps,
        prompt_path,
    )
    prompt = bundle.prompt

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
            return await _run_claude_prompt(
                prompt,
                workspace,
                t,
                system_prompt=bundle.system_prompt,
                max_turns=1,
                model=use_model,
                reasoning_effort=use_effort,
            )
        if b == "codex":
            text = await run_codex_prompt(
                prompt,
                workspace,
                t,
                model=use_model,
                reasoning_effort=use_effort,
            )
            return text, None
        if b == "gemini":
            text = await run_gemini_prompt(
                prompt,
                workspace,
                t,
                model=use_model,
            )
            return text, None
        raise RuntimeError(f"Unsupported reconciler backend: {b}")

    return await run_with_fallback(backends, _try, "reconcile", pr.url)
