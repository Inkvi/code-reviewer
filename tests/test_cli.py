import pytest
import typer

from pr_reviewer.cli import (
    _apply_bool_override,
    _apply_codex_backend_override,
    _apply_enabled_reviewer_override,
    _apply_field_override,
    _resolve_skip_overrides,
    _target_pr_urls_for_run_once,
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


def test_apply_bool_override_none_keeps_config() -> None:
    cfg = AppConfig(github_org="polymerdao", auto_post_review=False)

    out = _apply_bool_override(
        cfg,
        "auto_post_review",
        None,
        "--auto-post-review/--no-auto-post-review",
    )

    assert out.auto_post_review is False


def test_apply_bool_override_true() -> None:
    cfg = AppConfig(github_org="polymerdao", auto_post_review=False)

    out = _apply_bool_override(
        cfg,
        "auto_post_review",
        True,
        "--auto-post-review/--no-auto-post-review",
    )

    assert out.auto_post_review is True


def test_target_pr_urls_for_run_once_force_requires_url() -> None:
    with pytest.raises(typer.BadParameter):
        _target_pr_urls_for_run_once(
            None,
            force=True,
            use_saved_review=False,
            ignore_saved_review=False,
            ignore_existing_comment=False,
            ignore_head_sha=False,
        )


def test_target_pr_urls_for_run_once_use_saved_review_requires_url() -> None:
    with pytest.raises(typer.BadParameter):
        _target_pr_urls_for_run_once(
            None,
            force=False,
            use_saved_review=True,
            ignore_saved_review=False,
            ignore_existing_comment=False,
            ignore_head_sha=False,
        )


def test_target_pr_urls_for_run_once_ignore_head_sha_requires_url() -> None:
    with pytest.raises(typer.BadParameter):
        _target_pr_urls_for_run_once(
            None,
            force=False,
            use_saved_review=False,
            ignore_saved_review=False,
            ignore_existing_comment=False,
            ignore_head_sha=True,
        )


def test_target_pr_urls_for_run_once_use_saved_review_conflicts_with_force() -> None:
    with pytest.raises(typer.BadParameter):
        _target_pr_urls_for_run_once(
            ["https://github.com/polymerdao/obul/pull/1"],
            force=True,
            use_saved_review=True,
            ignore_saved_review=False,
            ignore_existing_comment=False,
            ignore_head_sha=False,
        )


def test_target_pr_urls_for_run_once_use_saved_review_conflicts_with_ignore_saved_review() -> None:
    with pytest.raises(typer.BadParameter):
        _target_pr_urls_for_run_once(
            ["https://github.com/polymerdao/obul/pull/1"],
            force=False,
            use_saved_review=True,
            ignore_saved_review=True,
            ignore_existing_comment=False,
            ignore_head_sha=False,
        )


def test_target_pr_urls_for_run_once_dedupes_values() -> None:
    urls = [
        "https://github.com/polymerdao/obul/pull/1",
        "https://github.com/polymerdao/obul/pull/1",
        "https://github.com/polymerdao/obul/pull/2",
    ]

    out = _target_pr_urls_for_run_once(
        urls,
        force=False,
        use_saved_review=False,
        ignore_saved_review=False,
        ignore_existing_comment=False,
        ignore_head_sha=False,
    )

    assert out == [
        "https://github.com/polymerdao/obul/pull/1",
        "https://github.com/polymerdao/obul/pull/2",
    ]


def test_resolve_skip_overrides_individual_flags() -> None:
    out = _resolve_skip_overrides(
        force=False,
        ignore_saved_review=True,
        ignore_existing_comment=False,
        ignore_head_sha=True,
    )

    assert out == (True, False, True)


def test_resolve_skip_overrides_force_enables_all() -> None:
    out = _resolve_skip_overrides(
        force=True,
        ignore_saved_review=False,
        ignore_existing_comment=False,
        ignore_head_sha=False,
    )

    assert out == (True, True, True)


def test_apply_enabled_reviewer_override_gemini_only() -> None:
    cfg = AppConfig(github_org="polymerdao")

    out = _apply_enabled_reviewer_override(cfg, ["gemini"])

    assert out.enabled_reviewers == ["gemini"]


def test_apply_field_override_gemini_model() -> None:
    cfg = AppConfig(github_org="polymerdao")

    out = _apply_field_override(
        cfg, "gemini_model", "gemini-3.1-pro-preview", "--gemini-model"
    )

    assert out.gemini_model == "gemini-3.1-pro-preview"
