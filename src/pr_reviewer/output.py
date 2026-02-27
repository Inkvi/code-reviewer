from __future__ import annotations

from pathlib import Path

from pr_reviewer.models import PRCandidate, ReviewerOutput


def write_review_markdown(
    output_root: Path,
    pr: PRCandidate,
    final_review: str,
) -> Path:
    target_dir = output_root / pr.owner / pr.repo
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"pr-{pr.number}.md"

    content = f"""### Automated Review: {pr.owner}/{pr.repo}#{pr.number}

- URL: {pr.url}
- Title: {pr.title}
- Base: `{pr.base_ref}`
- Head: `{pr.head_sha[:12]}`

{final_review.strip()}
"""

    file_path.write_text(content, encoding="utf-8")
    return file_path


def _section_text(value: str) -> str:
    text = value.strip()
    return text if text else "_No output_"


def _error_text(value: str | None) -> str:
    if value is None:
        return "_None_"
    cleaned = value.strip()
    return cleaned if cleaned else "_None_"


def write_reviewer_sidecar_markdown(
    output_root: Path,
    pr: PRCandidate,
    claude_output: ReviewerOutput,
    codex_output: ReviewerOutput,
    include_stderr: bool = True,
) -> Path:
    target_dir = output_root / pr.owner / pr.repo
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"pr-{pr.number}.raw.md"

    claude_stderr_section = (
        f"##### Claude STDERR\n\n{_section_text(claude_output.stderr)}\n"
        if include_stderr
        else "##### Claude STDERR\n\n_omitted by config_\n"
    )
    codex_stderr_section = (
        f"##### Codex STDERR\n\n{_section_text(codex_output.stderr)}\n"
        if include_stderr
        else "##### Codex STDERR\n\n_omitted by config_\n"
    )

    content = f"""### Reviewer Raw Outputs: {pr.owner}/{pr.repo}#{pr.number}

- URL: {pr.url}

#### Claude

- Status: `{claude_output.status}`
- Duration: `{claude_output.duration_seconds:.1f}s`
- Error: {_error_text(claude_output.error)}

##### Claude Markdown

{_section_text(claude_output.markdown)}

##### Claude STDOUT

{_section_text(claude_output.stdout)}

{claude_stderr_section}

#### Codex

- Status: `{codex_output.status}`
- Duration: `{codex_output.duration_seconds:.1f}s`
- Error: {_error_text(codex_output.error)}

##### Codex Markdown

{_section_text(codex_output.markdown)}

##### Codex STDOUT

{_section_text(codex_output.stdout)}

{codex_stderr_section}
"""
    file_path.write_text(content, encoding="utf-8")
    return file_path
