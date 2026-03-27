from __future__ import annotations

import json
import logging
import re
import subprocess
from enum import Enum
from pathlib import Path

from code_reviewer.logger import info, warn
from code_reviewer.models import PRCandidate
from code_reviewer.prompts import PromptBundle, build_triage_bundle
from code_reviewer.reviewers._circuit_breaker import is_open as _cb_is_open
from code_reviewer.reviewers._circuit_breaker import record_failure as _cb_record_failure
from code_reviewer.reviewers._fallback import run_with_fallback
from code_reviewer.reviewers._sanitize import _escape_delimiters
from code_reviewer.reviewers.claude_cli import run_claude_cli_prompt
from code_reviewer.reviewers.claude_sdk import _run_claude_prompt
from code_reviewer.reviewers.codex_cli import run_codex_prompt
from code_reviewer.reviewers.gemini_cli import run_gemini_prompt
from code_reviewer.reviewers.opencode_cli import run_opencode_prompt

log = logging.getLogger(__name__)


class TriageResult(Enum):
    SIMPLE = "simple"
    FULL_REVIEW = "full_review"


_DIFF_MAX_LINES = 200


def _get_diff_snippet(workspace: Path, pr: PRCandidate) -> str:
    """Get a truncated diff from the workspace for triage context."""
    try:
        if pr.is_local and pr.review_mode == "uncommitted":
            cmd = ["git", "-C", str(workspace), "diff", "HEAD"]
        elif pr.is_local:
            cmd = ["git", "-C", str(workspace), "diff", f"{pr.base_ref}...HEAD"]
        else:
            cmd = ["git", "-C", str(workspace), "diff", f"origin/{pr.base_ref}...HEAD"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)  # noqa: S603
        diff = result.stdout
        if not diff:
            return ""
        lines = diff.splitlines()
        if len(lines) > _DIFF_MAX_LINES:
            return (
                "\n".join(lines[:_DIFF_MAX_LINES]) + f"\n... (truncated, {len(lines)} total lines)"
            )
        return diff
    except Exception:  # noqa: BLE001
        return ""


def _build_triage_prompt(pr: PRCandidate, diff_snippet: str = "") -> str:
    diff_section = _escape_delimiters(diff_snippet) if diff_snippet else ""
    return build_triage_bundle(pr, Path.cwd(), diff_section, None).prompt


def _parse_triage_response(text: str) -> TriageResult:
    # Try to extract JSON from markdown code blocks first
    code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block_match:
        text = code_block_match.group(1)

    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        # Try to find a JSON object in the response
        json_match = re.search(r"\{[^}]+\}", text)
        if json_match:
            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                return TriageResult.FULL_REVIEW
        else:
            return TriageResult.FULL_REVIEW

    raw = data.get("classification")
    if not isinstance(raw, str):
        return TriageResult.FULL_REVIEW
    if raw.strip().lower() == "simple":
        return TriageResult.SIMPLE
    return TriageResult.FULL_REVIEW


async def run_triage(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    backend: list[str] | str = "gemini",
    model: str | None = None,
    prompt_path: str | None = None,
    claude_backend: str = "sdk",
    gemini_fallback_model: str | None = None,
) -> tuple[TriageResult, PromptBundle]:
    backends = [backend] if isinstance(backend, str) else list(backend)
    diff_snippet = _get_diff_snippet(workspace, pr)
    diff_section = _escape_delimiters(diff_snippet) if diff_snippet else ""
    bundle = build_triage_bundle(pr, workspace, diff_section, prompt_path)
    prompt = bundle.prompt
    info(f"running triage (backends={' > '.join(backends)}, model={model or 'default'}) {pr.url}")

    # Resolve effective gemini model: if primary's circuit is open, use fallback
    _base_gemini_model = model if backends[0] == "gemini" else None
    _effective_gemini_model = _base_gemini_model
    if gemini_fallback_model and gemini_fallback_model != _base_gemini_model:
        opened, _ = _cb_is_open("gemini", _base_gemini_model)
        if opened:
            _effective_gemini_model = gemini_fallback_model

    async def _try(b: str) -> str:
        use_model = model if b == backends[0] else None
        if b == "claude":
            if claude_backend == "cli":
                text, _ = await run_claude_cli_prompt(
                    prompt,
                    workspace,
                    timeout_seconds,
                    system_prompt=bundle.system_prompt,
                    max_turns=1,
                    model=use_model,
                )
                return text
            text, _, _ = await _run_claude_prompt(
                prompt,
                workspace,
                timeout_seconds,
                system_prompt=bundle.system_prompt,
                max_turns=1,
                model=use_model,
            )
            return text
        if b == "codex":
            text, _ = await run_codex_prompt(
                prompt,
                workspace,
                timeout_seconds,
                model=use_model,
            )
            return text
        if b == "gemini":
            use_model = _effective_gemini_model
            try:
                return await run_gemini_prompt(
                    prompt,
                    workspace,
                    timeout_seconds,
                    model=use_model,
                )
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
                            "retrying gemini triage with fallback model %s %s",
                            gemini_fallback_model,
                            pr.url,
                        )
                        return await run_gemini_prompt(
                            prompt,
                            workspace,
                            timeout_seconds,
                            model=gemini_fallback_model,
                        )
                raise
        if b == "opencode":
            text, _ = await run_opencode_prompt(
                prompt,
                workspace,
                timeout_seconds,
                model=use_model,
            )
            return text
        raise RuntimeError(f"unsupported triage backend: {b}")

    try:
        models_map: dict[str, str | None] = {}
        for b in backends:
            models_map[b] = (
                _effective_gemini_model if b == "gemini" else (model if b == backends[0] else None)
            )
        text = await run_with_fallback(backends, _try, "triage", pr.url, models=models_map)
    except Exception as exc:  # noqa: BLE001
        warn(f"triage failed, falling back to full review: {exc} {pr.url}")
        return TriageResult.FULL_REVIEW, bundle

    result = _parse_triage_response(text)
    info(f"triage result: {result.value} {pr.url}")
    return result, bundle
