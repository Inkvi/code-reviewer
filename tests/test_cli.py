import pytest
import typer
from typer.testing import CliRunner

from code_reviewer.cli import (
    _apply_bool_override,
    _apply_codex_backend_override,
    _apply_enabled_reviewer_override,
    _apply_field_override,
    _target_pr_urls_for_run_once,
    app,
)
from code_reviewer.config import AppConfig


def test_apply_enabled_reviewer_override_none_keeps_config() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    out = _apply_enabled_reviewer_override(cfg, None)

    assert out.enabled_reviewers == ["claude", "codex"]


def test_apply_enabled_reviewer_override_codex_only() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    out = _apply_enabled_reviewer_override(cfg, ["codex"])

    assert out.enabled_reviewers == ["codex"]


def test_apply_enabled_reviewer_override_invalid_raises_bad_parameter() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    with pytest.raises(typer.BadParameter):
        _apply_enabled_reviewer_override(cfg, ["invalid"])


def test_apply_codex_backend_override_none_keeps_config() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    out = _apply_codex_backend_override(cfg, None)

    assert out.codex_backend == "cli"


def test_apply_codex_backend_override_agents_sdk() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    out = _apply_codex_backend_override(cfg, "agents_sdk")

    assert out.codex_backend == "agents_sdk"


def test_apply_codex_backend_override_invalid_raises_bad_parameter() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    with pytest.raises(typer.BadParameter):
        _apply_codex_backend_override(cfg, "invalid")


def test_apply_field_override_codex_model() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    out = _apply_field_override(cfg, "codex_model", "gpt-5.3-codex-mini", "--codex-model")

    assert out.codex_model == "gpt-5.3-codex-mini"


def test_apply_field_override_invalid_reasoning_raises_bad_parameter() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    with pytest.raises(typer.BadParameter):
        _apply_field_override(
            cfg,
            "codex_reasoning_effort",
            "max",
            "--codex-reasoning-effort",
        )


def test_apply_field_override_reconciler_model() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    out = _apply_field_override(
        cfg,
        "reconciler_model",
        "claude-sonnet-4-5",
        "--reconciler-model",
    )

    assert out.reconciler_model == "claude-sonnet-4-5"


def test_apply_field_override_reconciler_backend() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    out = _apply_field_override(
        cfg,
        "reconciler_backend",
        "codex",
        "--reconciler-backend",
    )

    assert out.reconciler_backend == "codex"


def test_apply_field_override_invalid_reconciler_backend_raises_bad_parameter() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    with pytest.raises(typer.BadParameter):
        _apply_field_override(
            cfg,
            "reconciler_backend",
            "invalid",
            "--reconciler-backend",
        )


def test_apply_field_override_invalid_reconciler_reasoning_raises_bad_parameter() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    with pytest.raises(typer.BadParameter):
        _apply_field_override(
            cfg,
            "reconciler_reasoning_effort",
            "invalid",
            "--reconciler-reasoning-effort",
        )


def test_apply_bool_override_none_keeps_config() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"], auto_post_review=False)

    out = _apply_bool_override(
        cfg,
        "auto_post_review",
        None,
        "--auto-post-review/--no-auto-post-review",
    )

    assert out.auto_post_review is False


def test_apply_bool_override_true() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"], auto_post_review=False)

    out = _apply_bool_override(
        cfg,
        "auto_post_review",
        True,
        "--auto-post-review/--no-auto-post-review",
    )

    assert out.auto_post_review is True


def test_target_pr_urls_for_run_once_use_saved_review_requires_url() -> None:
    with pytest.raises(typer.BadParameter):
        _target_pr_urls_for_run_once(
            None,
            use_saved_review=True,
        )


def test_target_pr_urls_for_run_once_dedupes_values() -> None:
    urls = [
        "https://github.com/polymerdao/obul/pull/1",
        "https://github.com/polymerdao/obul/pull/1",
        "https://github.com/polymerdao/obul/pull/2",
    ]

    out = _target_pr_urls_for_run_once(
        urls,
        use_saved_review=False,
    )

    assert out == [
        "https://github.com/polymerdao/obul/pull/1",
        "https://github.com/polymerdao/obul/pull/2",
    ]


def test_apply_enabled_reviewer_override_gemini_only() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    out = _apply_enabled_reviewer_override(cfg, ["gemini"])

    assert out.enabled_reviewers == ["gemini"]


def test_apply_field_override_gemini_model() -> None:
    cfg = AppConfig(github_orgs=["polymerdao"])

    out = _apply_field_override(
        cfg, "gemini_model", "gemini-3.1-pro-preview", "--gemini-model"
    )

    assert out.gemini_model == "gemini-3.1-pro-preview"


def test_output_format_json_requires_pr_url() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run-once", "--output-format", "json"])

    assert result.exit_code != 0
    assert "requires" in result.output.lower() or "pr-url" in result.output.lower()


def test_output_format_invalid_value_rejected() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run-once", "--output-format", "xml"])

    assert result.exit_code != 0


@pytest.mark.parametrize(
    "flag",
    [
        "--force",
        "--ignore-saved-review",
        "--ignore-existing-comment",
        "--ignore-head-sha",
    ],
)
def test_removed_skip_flags_are_rejected(flag: str) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run-once", flag])

    assert result.exit_code != 0
    assert "No such option" in result.output
