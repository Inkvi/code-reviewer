from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from code_reviewer.models import PRCandidate


def _versioned_stem(pr: PRCandidate, now: datetime | None = None) -> str:
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    timestamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    short_sha = pr.head_sha[:12] if pr.head_sha else "nohead"
    return f"{timestamp}-{short_sha}"


def _local_output_dirs(output_root: Path, pr: PRCandidate) -> tuple[Path, Path, str]:
    target_dir = output_root / "local" / pr.repo
    history_dir = target_dir / "history"
    stable_name = "review"
    return target_dir, history_dir, stable_name


def _pr_output_dirs(output_root: Path, pr: PRCandidate) -> tuple[Path, Path, str]:
    target_dir = output_root / pr.owner / pr.repo
    history_dir = target_dir / f"pr-{pr.number}"
    stable_name = f"pr-{pr.number}"
    return target_dir, history_dir, stable_name


def write_review_markdown(
    output_root: Path,
    pr: PRCandidate,
    final_review: str,
    *,
    version_label: str | None = None,
) -> Path:
    if pr.is_local:
        target_dir, history_dir, stable_name = _local_output_dirs(output_root, pr)
    else:
        target_dir, history_dir, stable_name = _pr_output_dirs(output_root, pr)
    target_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    stable_path = target_dir / f"{stable_name}.md"
    stem = version_label or _versioned_stem(pr)
    versioned_path = history_dir / f"{stem}.md"

    content = f"{final_review.strip()}\n"

    versioned_path.write_text(content, encoding="utf-8")
    stable_path.write_text(content, encoding="utf-8")
    return stable_path


def write_stage_markdown(
    output_root: Path,
    pr: PRCandidate,
    stage: str,
    content: str,
    *,
    version_label: str | None = None,
) -> Path:
    """Write a per-stage review output to its own markdown file.

    ``stage`` identifies the pipeline step, e.g. "lightweight", "claude",
    "codex", "gemini", or "reconcile".  Files are written as
    ``pr-{number}.{stage}.md`` (stable) and
    ``{version}.{stage}.md`` (versioned history).
    """
    target_dir, history_dir, stable_name = _pr_output_dirs(output_root, pr)
    target_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    stable_path = target_dir / f"{stable_name}.{stage}.md"
    stem = version_label or _versioned_stem(pr)
    versioned_path = history_dir / f"{stem}.{stage}.md"

    text = f"{content.strip()}\n"

    versioned_path.write_text(text, encoding="utf-8")
    stable_path.write_text(text, encoding="utf-8")
    return stable_path


def write_conversation_jsonl(
    output_root: Path,
    pr: PRCandidate,
    stage: str,
    events: list[dict],
    *,
    version_label: str | None = None,
) -> Path:
    """Write conversation events as JSONL (one JSON object per line).

    Files are written as ``pr-{number}.{stage}.conversation.jsonl`` (stable)
    and ``{version}.{stage}.conversation.jsonl`` (versioned history).
    """
    target_dir, history_dir, stable_name = _pr_output_dirs(output_root, pr)
    target_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    stable_path = target_dir / f"{stable_name}.{stage}.conversation.jsonl"
    stem = version_label or _versioned_stem(pr)
    versioned_path = history_dir / f"{stem}.{stage}.conversation.jsonl"

    lines = [json.dumps(event, separators=(",", ":")) for event in events]
    content = "\n".join(lines) + "\n" if lines else ""

    versioned_path.write_text(content, encoding="utf-8")
    stable_path.write_text(content, encoding="utf-8")
    return stable_path


def write_review_meta(
    output_root: Path,
    pr: PRCandidate,
    meta: dict[str, Any],
    *,
    version_label: str | None = None,
) -> None:
    """Write review metadata as JSON alongside the markdown artifacts.

    Writes ``pr-{number}.meta.json`` (stable) and
    ``{version}.meta.json`` (versioned history).
    """
    if pr.is_local:
        target_dir, history_dir, stable_name = _local_output_dirs(output_root, pr)
    else:
        target_dir, history_dir, stable_name = _pr_output_dirs(output_root, pr)
    target_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    stem = version_label or _versioned_stem(pr)
    payload = json.dumps(meta, indent=2) + "\n"
    (target_dir / f"{stable_name}.meta.json").write_text(payload, encoding="utf-8")
    (history_dir / f"{stem}.meta.json").write_text(payload, encoding="utf-8")
