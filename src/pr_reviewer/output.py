from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pr_reviewer.models import PRCandidate, ReviewerOutput


def write_review_markdown(
    output_root: Path,
    pr: PRCandidate,
    final_review: str,
    claude_output: ReviewerOutput,
    codex_output: ReviewerOutput,
) -> Path:
    target_dir = output_root / pr.owner / pr.repo
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"pr-{pr.number}.md"

    created_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    content = f"""# PR Review: {pr.owner}/{pr.repo}#{pr.number}

- URL: {pr.url}
- Title: {pr.title}
- Author: {pr.author_login}
- Base: {pr.base_ref}
- Head SHA: {pr.head_sha}
- Generated: {created_at}

## Final Reconciled Review

{final_review.strip()}

## Claude Raw Output

Status: {claude_output.status}

{claude_output.markdown.strip() or "_No output_"}

## Codex Raw Output

Status: {codex_output.status}

{codex_output.markdown.strip() or "_No output_"}
"""

    file_path.write_text(content, encoding="utf-8")
    return file_path
