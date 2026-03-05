from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path

from pr_reviewer.logger import info, warn
from pr_reviewer.models import PRCandidate
from pr_reviewer.reviewers.claude_sdk import _run_claude_prompt
from pr_reviewer.reviewers.codex_cli import run_codex_prompt
from pr_reviewer.reviewers.gemini_cli import run_gemini_prompt


class TriageResult(Enum):
    SIMPLE = "simple"
    FULL_REVIEW = "full_review"


_TRIAGE_PROMPT_TEMPLATE = """You are a PR triage classifier. Analyze this pull request and classify it as either "simple" or "full_review".

PR:
- URL: {url}
- Title: {title}
- Base: {base_ref}
- Files changed: {changed_files}
- Lines added: {additions}, deleted: {deletions}

A PR is "simple" if ALL of the following are true:
- Changes are limited to configuration values, version bumps, image tags, feature flags, environment variables, or dependency versions
- No new files containing business logic, application code, or algorithms
- No security-sensitive changes (secrets, authentication, authorization, permissions, network policies, cryptographic settings)
- No changes to CI/CD pipeline logic (adding/removing steps, changing build commands — simple value changes like image tags are fine)

If ANY of those conditions is NOT met, classify as "full_review".

Respond with ONLY a JSON object, no other text:
{{"classification": "simple"}} or {{"classification": "full_review"}}"""


def _build_triage_prompt(pr: PRCandidate) -> str:
    changed_files = ", ".join(pr.changed_file_paths) if pr.changed_file_paths else "unknown"
    return _TRIAGE_PROMPT_TEMPLATE.format(
        url=pr.url,
        title=pr.title,
        base_ref=pr.base_ref,
        changed_files=changed_files,
        additions=pr.additions,
        deletions=pr.deletions,
    )


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
    backend: str = "gemini",
    model: str | None = None,
) -> TriageResult:
    prompt = _build_triage_prompt(pr)
    info(f"running triage (backend={backend}, model={model or 'default'}) {pr.url}")

    try:
        if backend == "claude":
            text, _ = await _run_claude_prompt(
                prompt,
                workspace,
                timeout_seconds,
                system_prompt="You are a PR triage classifier. Respond only with JSON. Do not use tools.",
                max_turns=1,
                model=model,
            )
        elif backend == "codex":
            text = await run_codex_prompt(
                prompt, workspace, timeout_seconds, model=model,
            )
        elif backend == "gemini":
            text = await run_gemini_prompt(
                prompt, workspace, timeout_seconds, model=model,
            )
        else:
            warn(f"unsupported triage backend: {backend} {pr.url}")
            return TriageResult.FULL_REVIEW
    except Exception as exc:  # noqa: BLE001
        warn(f"triage failed, falling back to full review: {exc} {pr.url}")
        return TriageResult.FULL_REVIEW

    result = _parse_triage_response(text)
    info(f"triage result: {result.value} {pr.url}")
    return result
