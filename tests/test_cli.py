import pytest
import typer

from pr_reviewer.cli import (
    _apply_codex_backend_override,
    _apply_enabled_reviewer_override,
    _apply_field_override,
)
from pr_reviewer.config import AppConfig


def test_apply_enabled_reviewer_override_none_keeps_config() -> None:
    cfg = AppConfig(github_org="polymerdao")

    out = _apply_enabled_reviewer_override(cfg, None)

    assert out.enabled_reviewers == ["claude", "codex"]


def test_apply_enabled_reviewer_override_codex_only() -> None:
    cfg = AppConfig(github_org="polymerdao")

    out = _apply_enabled_reviewer_override(cfg, ["codex"])

    assert out.enabled_reviewers == ["codex"]


def test_apply_enabled_reviewer_override_invalid_raises_bad_parameter() -> None:
    cfg = AppConfig(github_org="polymerdao")

    with pytest.raises(typer.BadParameter):
        _apply_enabled_reviewer_override(cfg, ["invalid"])


def test_apply_codex_backend_override_none_keeps_config() -> None:
    cfg = AppConfig(github_org="polymerdao")

    out = _apply_codex_backend_override(cfg, None)

    assert out.codex_backend == "cli"


def test_apply_codex_backend_override_agents_sdk() -> None:
    cfg = AppConfig(github_org="polymerdao")

    out = _apply_codex_backend_override(cfg, "agents_sdk")

    assert out.codex_backend == "agents_sdk"


def test_apply_codex_backend_override_invalid_raises_bad_parameter() -> None:
    cfg = AppConfig(github_org="polymerdao")

    with pytest.raises(typer.BadParameter):
        _apply_codex_backend_override(cfg, "invalid")


def test_apply_field_override_codex_model() -> None:
    cfg = AppConfig(github_org="polymerdao")

    out = _apply_field_override(cfg, "codex_model", "gpt-5.3-codex-mini", "--codex-model")

    assert out.codex_model == "gpt-5.3-codex-mini"


def test_apply_field_override_invalid_reasoning_raises_bad_parameter() -> None:
    cfg = AppConfig(github_org="polymerdao")

    with pytest.raises(typer.BadParameter):
        _apply_field_override(
            cfg,
            "codex_reasoning_effort",
            "max",
            "--codex-reasoning-effort",
        )
