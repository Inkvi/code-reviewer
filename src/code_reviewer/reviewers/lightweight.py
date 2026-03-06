from __future__ import annotations

from pathlib import Path

from code_reviewer.logger import info
from code_reviewer.models import PRCandidate, TokenUsage
from code_reviewer.reviewers.claude_sdk import _run_claude_prompt
from code_reviewer.reviewers.codex_cli import run_codex_prompt
from code_reviewer.reviewers.gemini_cli import run_gemini_prompt

_LIGHTWEIGHT_REVIEW_PROMPT_TEMPLATE = """You are reviewing a simple configuration or infrastructure pull request. Perform a focused checklist review.

PR:
- {url_label}: {url}
- Title: {title}
- Base: {base_ref}
- Head SHA: {head_sha}
- Files changed: {changed_files}
- Lines added: {additions}, deleted: {deletions}

Review checklist — evaluate each item:
1. **Syntax & format**: Are the changed files valid and well-formed? (YAML indentation, JSON brackets, TOML syntax, etc.)
2. **Secrets & credentials**: Are there any hardcoded secrets, API keys, passwords, or tokens?
3. **Environment correctness**: Are there environment-specific values (hostnames, IPs, ports) that don't belong in this branch/environment?
4. **Breaking changes**: Are any keys removed, fields renamed, ports changed, or defaults altered that could break existing consumers?
5. **Version validity**: For version bumps or image tag changes, is the new version/tag a real, expected value?

Strict output rules:
- Keep total output under 150 words.
- No tables, no long summary, no praise/filler.
- Include only these sections in this exact order:
  1) `### Findings`
  2) `### Test Gaps`
- `### Findings`:
  - 0-5 bullets, highest severity first.
  - Severity: P1 (breaks production/security), P2 (correctness issue), P3 (minor/style).
  - Each bullet: `- [P1|P2|P3] path[:line] - issue. Impact. Fix.`
  - If no material issues: `- No material findings.`
- `### Test Gaps`:
  - 0-2 bullets with concrete missing tests.
  - If none: `- None noted.`
- Do not invent evidence. If uncertain, omit.
- Do not use tools."""


def _build_lightweight_prompt(pr: PRCandidate) -> str:
    changed_files = ", ".join(pr.changed_file_paths) if pr.changed_file_paths else "unknown"
    url_label = "Repository" if pr.is_local else "URL"
    return _LIGHTWEIGHT_REVIEW_PROMPT_TEMPLATE.format(
        url_label=url_label,
        url=pr.url,
        title=pr.title,
        base_ref=pr.base_ref,
        head_sha=pr.head_sha,
        changed_files=changed_files,
        additions=pr.additions,
        deletions=pr.deletions,
    )


async def run_lightweight_review(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    *,
    backend: str = "claude",
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> tuple[str, TokenUsage | None]:
    prompt = _build_lightweight_prompt(pr)
    info(
        f"running lightweight review "
        f"(backend={backend}, model={model or 'default'}) {pr.url}"
    )

    if backend == "claude":
        return await _run_claude_prompt(
            prompt,
            workspace,
            timeout_seconds,
            system_prompt=(
                "You are a lightweight code reviewer for configuration and infrastructure changes. "
                "Respond only with the requested markdown sections. Do not use any tools."
            ),
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
