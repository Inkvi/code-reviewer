from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pr_reviewer.models import PRCandidate, ReviewerOutput


def _versioned_stem(pr: PRCandidate, now: datetime | None = None) -> str:
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    short_sha = pr.head_sha[:12] if pr.head_sha else "nohead"
    return f"{timestamp}-{short_sha}"


def write_review_markdown(
    output_root: Path,
    pr: PRCandidate,
    final_review: str,
    *,
    version_label: str | None = None,
) -> Path:
    target_dir = output_root / pr.owner / pr.repo
    history_dir = target_dir / f"pr-{pr.number}"
    target_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    stable_path = target_dir / f"pr-{pr.number}.md"
    stem = version_label or _versioned_stem(pr)
    versioned_path = history_dir / f"{stem}.md"

    content = f"{final_review.strip()}\n"

    versioned_path.write_text(content, encoding="utf-8")
    stable_path.write_text(content, encoding="utf-8")
    return stable_path


def _section_text(value: str) -> str:
    text = value.strip()
    return text if text else "_No output_"


def _error_text(value: str | None) -> str:
    if value is None:
        return "_None_"
    cleaned = value.strip()
    return cleaned if cleaned else "_None_"


def _render_reviewer_section(
    name: str,
    output: ReviewerOutput,
    include_stderr: bool,
) -> str:
    display_name = name.capitalize()
    stderr_section = (
        f"##### {display_name} STDERR\n\n{_section_text(output.stderr)}\n"
        if include_stderr
        else f"##### {display_name} STDERR\n\n_omitted by config_\n"
    )

    return f"""#### {display_name}

- Status: `{output.status}`
- Duration: `{output.duration_seconds:.1f}s`
- Error: {_error_text(output.error)}

##### {display_name} Markdown

{_section_text(output.markdown)}

##### {display_name} STDOUT

{_section_text(output.stdout)}

{stderr_section}
"""


def write_reviewer_sidecar_markdown(
    output_root: Path,
    pr: PRCandidate,
    reviewer_outputs: dict[str, ReviewerOutput],
    include_stderr: bool = True,
    *,
    version_label: str | None = None,
) -> Path:
    target_dir = output_root / pr.owner / pr.repo
    history_dir = target_dir / f"pr-{pr.number}"
    target_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    stable_path = target_dir / f"pr-{pr.number}.raw.md"
    stem = version_label or _versioned_stem(pr)
    versioned_path = history_dir / f"{stem}.raw.md"

    # Render in a stable order: claude, codex, gemini, then any others alphabetically
    preferred_order = ["claude", "codex", "gemini"]
    ordered_names = [n for n in preferred_order if n in reviewer_outputs]
    ordered_names += sorted(n for n in reviewer_outputs if n not in preferred_order)

    sections: list[str] = []
    for name in ordered_names:
        sections.append(_render_reviewer_section(name, reviewer_outputs[name], include_stderr))

    content = f"""### Reviewer Raw Outputs: {pr.owner}/{pr.repo}#{pr.number}

- URL: {pr.url}

{"".join(sections)}"""

    versioned_path.write_text(content, encoding="utf-8")
    stable_path.write_text(content, encoding="utf-8")
    return stable_path
