from __future__ import annotations

import json
import re
import subprocess
from enum import Enum
from pathlib import Path

from code_reviewer.logger import info, warn
from code_reviewer.models import PRCandidate
from code_reviewer.prompts import build_triage_bundle
from code_reviewer.reviewers._fallback import run_with_fallback
from code_reviewer.reviewers._sanitize import _escape_delimiters
from code_reviewer.reviewers.claude_sdk import _run_claude_prompt
from code_reviewer.reviewers.codex_cli import run_codex_prompt
from code_reviewer.reviewers.gemini_cli import run_gemini_prompt


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
    if diff_snippet:
        diff_section = (
            "\n<untrusted_data type='diff'>\n"
            f"{_escape_delimiters(diff_snippet)}\n"
            "</untrusted_data>\n"
        )
    else:
        diff_section = ""

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
) -> TriageResult:
    backends = [backend] if isinstance(backend, str) else list(backend)
    diff_snippet = _get_diff_snippet(workspace, pr)
    if diff_snippet:
        diff_section = (
            "\n<untrusted_data type='diff'>\n"
            f"{_escape_delimiters(diff_snippet)}\n"
            "</untrusted_data>\n"
        )
    else:
        diff_section = ""
    bundle = build_triage_bundle(pr, workspace, diff_section, prompt_path)
    prompt = bundle.prompt
    info(f"running triage (backends={' > '.join(backends)}, model={model or 'default'}) {pr.url}")

    async def _try(b: str) -> str:
        use_model = model if b == backends[0] else None
        if b == "claude":
            text, _ = await _run_claude_prompt(
                prompt,
                workspace,
                timeout_seconds,
                system_prompt=bundle.system_prompt,
                max_turns=1,
                model=use_model,
            )
            return text
        if b == "codex":
            return await run_codex_prompt(
                prompt,
                workspace,
                timeout_seconds,
                model=use_model,
            )
        if b == "gemini":
            return await run_gemini_prompt(
                prompt,
                workspace,
                timeout_seconds,
                model=use_model,
            )
        raise RuntimeError(f"unsupported triage backend: {b}")

    try:
        text = await run_with_fallback(backends, _try, "triage", pr.url)
    except Exception as exc:  # noqa: BLE001
        warn(f"triage failed, falling back to full review: {exc} {pr.url}")
        return TriageResult.FULL_REVIEW

    result = _parse_triage_response(text)
    info(f"triage result: {result.value} {pr.url}")
    return result
