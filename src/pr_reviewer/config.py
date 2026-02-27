from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator


class AppConfig(BaseModel):
    github_org: str = Field(min_length=1)
    poll_interval_seconds: int = Field(default=60, ge=15)
    excluded_repos: list[str] = Field(default_factory=list)
    skip_own_prs: bool = True
    auto_post_review: bool = False
    post_mode: str = "pr_comment"
    output_dir: str = "./reviews"
    state_file: str = "./.state/pr-reviewer-state.json"
    clone_root: str = "./.tmp/workspaces"
    claude_timeout_seconds: int = Field(default=900, ge=30)
    codex_timeout_seconds: int = Field(default=900, ge=30)
    max_parallel_prs: int = Field(default=1, ge=1)

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


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("rb") as handle:
        data = tomllib.load(handle)

    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid config in {path}: {exc}") from exc
