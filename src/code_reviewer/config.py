from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from code_reviewer.prompts import PromptStep, validate_prompt_override_file

_ALLOWED_BACKENDS = {"claude", "codex", "gemini"}


def _normalize_backend_list(value: str | list[str], field_name: str) -> list[str]:
    """Normalize a single string or list of strings into a deduplicated backend list."""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError(f"{field_name} must be a string or list of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for entry in items:
        if not isinstance(entry, str):
            raise ValueError(f"{field_name} entries must be strings")
        cleaned = entry.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        if cleaned not in _ALLOWED_BACKENDS:
            raise ValueError(f"{field_name} entries must be one of: claude, codex, gemini")
        seen.add(cleaned)
        normalized.append(cleaned)
    if not normalized:
        raise ValueError(f"{field_name} must include at least one backend")
    return normalized


_PROMPT_PATH_FIELD_TO_STEP: dict[str, PromptStep] = {
    "triage_prompt_path": "triage",
    "lightweight_review_prompt_path": "lightweight_review",
    "full_review_prompt_path": "full_review",
    "reconcile_prompt_path": "reconcile",
}


class AppConfig(BaseModel):
    github_orgs: list[str] = Field(default_factory=list)
    poll_interval_seconds: int = Field(default=60, ge=15)
    excluded_repos: list[str] = Field(default_factory=list)
    enabled_reviewers: list[str] = Field(default_factory=lambda: ["claude", "codex"])
    claude_model: str | None = None
    claude_reasoning_effort: str | None = None
    reconciler_backend: list[str] = Field(default_factory=lambda: ["claude"])
    reconciler_model: str | None = None
    reconciler_reasoning_effort: str | None = None
    codex_backend: str = "cli"
    codex_model: str = Field(default="gpt-5.3-codex", min_length=1)
    codex_reasoning_effort: str | None = "low"
    gemini_model: str | None = None
    gemini_timeout_seconds: int = Field(default=900, ge=30)
    skip_own_prs: bool = True
    auto_post_review: bool = False
    auto_submit_review_decision: bool = False
    include_reviewer_stderr: bool = True
    post_mode: str = "pr_comment"
    output_dir: str = "./reviews"
    state_file: str = "./.state/code-reviewer-state.json"
    clone_root: str = "./.tmp/workspaces"
    claude_timeout_seconds: int = Field(default=900, ge=30)
    codex_timeout_seconds: int = Field(default=900, ge=30)
    max_parallel_prs: int = Field(default=1, ge=1)
    trigger_mode: str = "rerequest_only"
    max_mid_review_restarts: int = Field(default=2, ge=0, le=5)
    max_findings: int = Field(default=10, ge=1, le=20)
    max_test_gaps: int = Field(default=3, ge=1, le=10)
    post_rerequest_comment: bool = True
    slash_command_enabled: bool = True

    # Triage
    triage_backend: list[str] = Field(default_factory=lambda: ["gemini"])
    triage_model: str | None = "gemini-3-flash-preview"
    triage_timeout_seconds: int = Field(default=60, ge=10)

    # Lightweight review
    lightweight_review_backend: list[str] = Field(default_factory=lambda: ["gemini"])
    lightweight_review_model: str | None = "gemini-3-flash-preview"
    lightweight_review_reasoning_effort: str | None = None
    lightweight_review_timeout_seconds: int = Field(default=300, ge=30)
    triage_prompt_path: str | None = None
    lightweight_review_prompt_path: str | None = None
    full_review_prompt_path: str | None = None
    reconcile_prompt_path: str | None = None

    @property
    def github_owners(self) -> list[str]:
        return list(self.github_orgs)

    @field_validator("github_orgs")
    @classmethod
    def normalize_github_orgs(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for entry in value:
            cleaned = entry.strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(cleaned)
        return normalized

    @field_validator("post_mode")
    @classmethod
    def validate_post_mode(cls, value: str) -> str:
        if value != "pr_comment":
            raise ValueError("post_mode must be 'pr_comment'")
        return value

    @field_validator("excluded_repos")
    @classmethod
    def normalize_excluded_repos(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for entry in value:
            cleaned = entry.strip().lower()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            normalized.append(cleaned)
        return normalized

    @field_validator("enabled_reviewers")
    @classmethod
    def validate_enabled_reviewers(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        allowed = {"claude", "codex", "gemini"}
        for entry in value:
            reviewer = entry.strip().lower()
            if not reviewer or reviewer in seen:
                continue
            if reviewer not in allowed:
                raise ValueError("enabled_reviewers entries must be one of: claude, codex, gemini")
            seen.add(reviewer)
            normalized.append(reviewer)
        if not normalized:
            raise ValueError("enabled_reviewers must include at least one reviewer")
        return normalized

    @field_validator("codex_backend")
    @classmethod
    def validate_codex_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"cli", "agents_sdk"}:
            raise ValueError("codex_backend must be one of: cli, agents_sdk")
        return normalized

    @field_validator("reconciler_backend", mode="before")
    @classmethod
    def validate_reconciler_backend(cls, value: str | list[str]) -> list[str]:
        return _normalize_backend_list(value, "reconciler_backend")

    @field_validator("claude_reasoning_effort")
    @classmethod
    def validate_claude_reasoning_effort(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"low", "medium", "high", "max"}:
            raise ValueError("claude_reasoning_effort must be one of: low, medium, high, max")
        return normalized

    @field_validator("reconciler_reasoning_effort")
    @classmethod
    def validate_reconciler_reasoning_effort(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"low", "medium", "high", "max"}:
            raise ValueError("reconciler_reasoning_effort must be one of: low, medium, high, max")
        return normalized

    @field_validator("codex_reasoning_effort")
    @classmethod
    def validate_codex_reasoning_effort(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"low", "medium", "high"}:
            raise ValueError("codex_reasoning_effort must be one of: low, medium, high")
        return normalized

    @field_validator("triage_backend", mode="before")
    @classmethod
    def validate_triage_backend(cls, value: str | list[str]) -> list[str]:
        return _normalize_backend_list(value, "triage_backend")

    @field_validator("triage_model")
    @classmethod
    def validate_triage_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("triage_model cannot be empty")
        return cleaned

    @field_validator("lightweight_review_backend", mode="before")
    @classmethod
    def validate_lightweight_review_backend(cls, value: str | list[str]) -> list[str]:
        return _normalize_backend_list(value, "lightweight_review_backend")

    @field_validator("lightweight_review_model")
    @classmethod
    def validate_lightweight_review_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("lightweight_review_model cannot be empty")
        return cleaned

    @field_validator("lightweight_review_reasoning_effort")
    @classmethod
    def validate_lightweight_review_reasoning_effort(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"low", "medium", "high", "max"}:
            raise ValueError(
                "lightweight_review_reasoning_effort must be one of: low, medium, high, max"
            )
        return normalized

    @field_validator(
        "triage_prompt_path",
        "lightweight_review_prompt_path",
        "full_review_prompt_path",
        "reconcile_prompt_path",
    )
    @classmethod
    def validate_prompt_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("prompt override path cannot be empty")
        return cleaned

    @field_validator("claude_model")
    @classmethod
    def validate_claude_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("claude_model cannot be empty")
        return cleaned

    @field_validator("reconciler_model")
    @classmethod
    def validate_reconciler_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reconciler_model cannot be empty")
        return cleaned

    @field_validator("gemini_model")
    @classmethod
    def validate_gemini_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("gemini_model cannot be empty")
        return cleaned

    @field_validator("trigger_mode")
    @classmethod
    def validate_trigger_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"rerequest_only", "rerequest_or_commit"}:
            raise ValueError("trigger_mode must be one of: rerequest_only, rerequest_or_commit")
        return normalized

    @model_validator(mode="after")
    def validate_github_owner_settings(self) -> AppConfig:
        owners: list[str] = []
        seen: set[str] = set()
        for owner in self.github_orgs:
            key = owner.lower()
            if key in seen:
                continue
            seen.add(key)
            owners.append(owner)

        self.github_orgs = owners
        return self

    @model_validator(mode="after")
    def validate_reconciler_backend_settings(self) -> AppConfig:
        if self.reconciler_reasoning_effort == "max" and "codex" in self.reconciler_backend:
            raise ValueError(
                "reconciler_reasoning_effort must be one of: low, medium, high "
                "when codex is in reconciler_backend"
            )
        return self

    @model_validator(mode="after")
    def validate_lightweight_review_backend_settings(self) -> AppConfig:
        if (
            self.lightweight_review_reasoning_effort == "max"
            and "codex" in self.lightweight_review_backend
        ):
            raise ValueError(
                "lightweight_review_reasoning_effort must be one of: low, medium, high "
                "when codex is in lightweight_review_backend"
            )
        return self


def default_config() -> AppConfig:
    """Return an AppConfig with all default values (no config file needed)."""
    return AppConfig()


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("rb") as handle:
        data = tomllib.load(handle)

    if "github_org" in data:
        raise ValueError("Invalid config: github_org is no longer supported; use github_orgs")

    _resolve_prompt_override_paths(data, path.parent)

    try:
        config = AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid config in {path}: {exc}") from exc
    _validate_prompt_overrides(config, path)
    return config


def _resolve_prompt_override_paths(data: dict[str, object], base_dir: Path) -> None:
    for field in _PROMPT_PATH_FIELD_TO_STEP:
        raw_value = data.get(field)
        if raw_value is None or not isinstance(raw_value, str):
            continue
        cleaned = raw_value.strip()
        if not cleaned:
            data[field] = cleaned
            continue
        candidate = Path(cleaned)
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve()
        data[field] = str(candidate)


def _validate_prompt_overrides(config: AppConfig, config_path: Path) -> None:
    for field, step in _PROMPT_PATH_FIELD_TO_STEP.items():
        raw_path = getattr(config, field)
        if raw_path is None:
            continue
        try:
            validate_prompt_override_file(Path(raw_path), step=step)
        except ValueError as exc:
            raise ValueError(f"Invalid config in {config_path}: {exc}") from exc
