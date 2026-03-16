from __future__ import annotations

import string
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from code_reviewer.models import PRCandidate, ReviewerOutput

PromptStep = Literal["triage", "lightweight_review", "full_review", "reconcile"]

_PROMPT_SPEC_KEYS = frozenset({"prompt", "system_prompt"})
_FORMATTER = string.Formatter()


class PromptOverrideError(ValueError):
    """Raised when a configured prompt override is invalid."""


@dataclass(slots=True, frozen=True)
class PromptBundle:
    prompt: str
    system_prompt: str | None = None


def _escape_delimiters(text: str) -> str:
    return text.replace("<untrusted_data", "&lt;untrusted_data").replace(
        "</untrusted_data", "&lt;/untrusted_data"
    )


def _format_pr_comments(pr_comments: list[str]) -> str:
    if not pr_comments:
        return "_None provided._"
    sections = [f"- {_escape_delimiters(entry)}" for entry in pr_comments]
    return "\n".join(sections)


_DEFAULT_PROMPT_SPEC_DIR = Path(__file__).with_name("prompt_specs")
_DEFAULT_PROMPT_SPEC_FILES: dict[PromptStep, str] = {
    "triage": "triage.toml",
    "lightweight_review": "lightweight_review.toml",
    "full_review": "full_review.toml",
    "reconcile": "reconcile.toml",
}

_ALLOWED_PLACEHOLDERS: dict[PromptStep, set[str]] = {
    "triage": {
        "url_label",
        "url",
        "title",
        "description",
        "base_ref",
        "head_sha",
        "changed_files",
        "additions",
        "deletions",
        "workspace",
        "diff_section",
        "pr_comments",
    },
    "lightweight_review": {
        "url_label",
        "url",
        "title",
        "description",
        "base_ref",
        "head_sha",
        "changed_files",
        "additions",
        "deletions",
        "workspace",
        "diff_section",
        "pr_comments",
    },
    "full_review": {
        "url_label",
        "url",
        "title",
        "description",
        "base_ref",
        "head_sha",
        "changed_files",
        "additions",
        "deletions",
        "workspace",
        "pr_comments",
    },
    "reconcile": {
        "url_label",
        "url",
        "title",
        "description",
        "base_ref",
        "head_sha",
        "changed_files",
        "additions",
        "deletions",
        "workspace",
        "pr_comments",
        "reviewer_sources",
        "max_findings",
        "max_test_gaps",
    },
}


def _extract_placeholder_names(template: str) -> set[str]:
    names: set[str] = set()
    for _, field_name, _, _ in _FORMATTER.parse(template):
        if not field_name:
            continue
        root_name = field_name.split(".", 1)[0].split("[", 1)[0]
        names.add(root_name)
    return names


def _normalize_optional_text(value: object, *, path: Path, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PromptOverrideError(f"{path}: `{key}` must be a string if provided.")
    cleaned = value.strip()
    return cleaned or None


def _validate_placeholders(bundle: PromptBundle, *, step: PromptStep, source: str) -> None:
    allowed = _ALLOWED_PLACEHOLDERS[step]
    for key, value in (
        ("prompt", bundle.prompt),
        ("system_prompt", bundle.system_prompt),
    ):
        if not value:
            continue
        unknown = sorted(_extract_placeholder_names(value) - allowed)
        if unknown:
            raise PromptOverrideError(
                f"{source}: `{key}` contains unknown placeholders for step `{step}`: "
                f"{', '.join(unknown)}"
            )


def _bundle_from_raw(data: object, *, path: Path, step: PromptStep) -> PromptBundle:
    if not isinstance(data, dict):
        raise PromptOverrideError(f"{path}: prompt spec must be a TOML table.")
    unknown_keys = sorted(set(data) - _PROMPT_SPEC_KEYS)
    if unknown_keys:
        raise PromptOverrideError(f"{path}: unknown prompt-spec keys: {', '.join(unknown_keys)}")
    raw_prompt = data.get("prompt")
    if not isinstance(raw_prompt, str) or not raw_prompt.strip():
        raise PromptOverrideError(f"{path}: `prompt` is required and must be a non-empty string.")

    bundle = PromptBundle(
        prompt=raw_prompt.strip(),
        system_prompt=_normalize_optional_text(
            data.get("system_prompt"), path=path, key="system_prompt"
        ),
    )
    _validate_placeholders(bundle, step=step, source=str(path))
    return bundle


def validate_prompt_override_file(path: Path, *, step: PromptStep) -> None:
    load_prompt_bundle(path, step=step)


def get_default_prompt_spec_path(step: PromptStep) -> Path:
    return (_DEFAULT_PROMPT_SPEC_DIR / _DEFAULT_PROMPT_SPEC_FILES[step]).resolve()


@cache
def get_default_prompt_bundle(step: PromptStep) -> PromptBundle:
    bundle = load_prompt_bundle(get_default_prompt_spec_path(step), step=step)
    _validate_placeholders(bundle, step=step, source=f"default:{step}")
    return bundle


def load_prompt_bundle(path: Path, *, step: PromptStep) -> PromptBundle:
    try:
        with path.open("rb") as handle:
            raw_data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise PromptOverrideError(f"{path}: prompt spec file not found.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PromptOverrideError(f"{path}: invalid TOML: {exc}") from exc

    return _bundle_from_raw(raw_data, path=path, step=step)


def get_prompt_bundle(step: PromptStep, prompt_path: str | None) -> PromptBundle:
    if prompt_path is None:
        return get_default_prompt_bundle(step)
    return load_prompt_bundle(Path(prompt_path), step=step)


def render_prompt_bundle(
    bundle: PromptBundle,
    *,
    step: PromptStep,
    values: Mapping[str, object],
) -> PromptBundle:
    _validate_placeholders(bundle, step=step, source=f"render:{step}")

    def _render(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return value.format(**values)
        except KeyError as exc:
            missing = exc.args[0]
            raise PromptOverrideError(
                f"Missing placeholder value `{missing}` while rendering step `{step}`."
            ) from exc

    return PromptBundle(
        prompt=_render(bundle.prompt) or "",
        system_prompt=_render(bundle.system_prompt),
    )


def _common_values(pr: PRCandidate, workspace: Path) -> dict[str, object]:
    changed_files = ", ".join(pr.changed_file_paths) if pr.changed_file_paths else "unknown"
    return {
        "url_label": "Repository" if pr.is_local else "URL",
        "url": pr.url,
        "title": _escape_delimiters(pr.title),
        "description": _escape_delimiters(pr.description.strip()),
        "base_ref": pr.base_ref,
        "head_sha": pr.head_sha,
        "changed_files": _escape_delimiters(changed_files),
        "additions": pr.additions,
        "deletions": pr.deletions,
        "workspace": str(workspace.resolve()),
        "pr_comments": _format_pr_comments(pr.pr_comments),
    }


def build_triage_bundle(
    pr: PRCandidate, workspace: Path, diff_section: str, prompt_path: str | None
) -> PromptBundle:
    bundle = get_prompt_bundle("triage", prompt_path)
    values = _common_values(pr, workspace)
    values["diff_section"] = diff_section
    return render_prompt_bundle(bundle, step="triage", values=values)


def build_lightweight_bundle(
    pr: PRCandidate, workspace: Path, diff_section: str, prompt_path: str | None
) -> PromptBundle:
    bundle = get_prompt_bundle("lightweight_review", prompt_path)
    values = _common_values(pr, workspace)
    values["diff_section"] = diff_section
    return render_prompt_bundle(bundle, step="lightweight_review", values=values)


def build_full_review_bundle(
    pr: PRCandidate, workspace: Path, prompt_path: str | None
) -> PromptBundle:
    bundle = get_prompt_bundle("full_review", prompt_path)
    return render_prompt_bundle(bundle, step="full_review", values=_common_values(pr, workspace))


def _format_reviewer_sources(reviewer_outputs: list[ReviewerOutput]) -> str:
    source_sections: list[str] = []
    for i, output in enumerate(reviewer_outputs):
        letter = chr(ord("A") + i)
        label = output.reviewer.capitalize()
        if output.status != "ok":
            formatted = f"{label} failed: {output.error or 'unknown error'}"
        else:
            formatted = output.markdown or f"{label} returned no content"
        source_sections.append(
            f"<untrusted_data>\n"
            f"Source {letter} ({label}):\n{_escape_delimiters(formatted)}\n"
            f"</untrusted_data>"
        )
    return "\n\n".join(source_sections)


def build_reconcile_bundle(
    pr: PRCandidate,
    workspace: Path,
    reviewer_outputs: list[ReviewerOutput],
    max_findings: int,
    max_test_gaps: int,
    prompt_path: str | None,
) -> PromptBundle:
    bundle = get_prompt_bundle("reconcile", prompt_path)
    values = _common_values(pr, workspace)
    values.update(
        {
            "reviewer_sources": _format_reviewer_sources(reviewer_outputs),
            "max_findings": max_findings,
            "max_test_gaps": max_test_gaps,
        }
    )
    return render_prompt_bundle(bundle, step="reconcile", values=values)
