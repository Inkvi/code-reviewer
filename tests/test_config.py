from pathlib import Path

import pytest

from pr_reviewer.config import load_config


def test_load_config_success(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["Inkvi"]\n', encoding="utf-8")

    cfg = load_config(path)

    assert cfg.github_orgs == ["Inkvi"]
    assert cfg.github_owners == ["Inkvi"]
    assert cfg.poll_interval_seconds == 60
    assert cfg.auto_post_review is False
    assert cfg.auto_submit_review_decision is False
    assert cfg.include_reviewer_stderr is True
    assert cfg.excluded_repos == []
    assert cfg.enabled_reviewers == ["claude", "codex"]
    assert cfg.claude_model is None
    assert cfg.claude_reasoning_effort is None
    assert cfg.reconciler_backend == "claude"
    assert cfg.reconciler_model is None
    assert cfg.reconciler_reasoning_effort is None
    assert cfg.codex_backend == "cli"
    assert cfg.codex_model == "gpt-5.3-codex"
    assert cfg.codex_reasoning_effort == "low"
    assert cfg.gemini_model is None
    assert cfg.gemini_timeout_seconds == 900
    assert cfg.trigger_mode == "rerequest_only"


def test_load_config_invalid_interval(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["Inkvi"]\npoll_interval_seconds = 1\n', encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_accepts_github_orgs_only(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["polymerdao", "Inkvi"]\n', encoding="utf-8")

    cfg = load_config(path)

    assert cfg.github_orgs == ["polymerdao", "Inkvi"]
    assert cfg.github_owners == ["polymerdao", "Inkvi"]


def test_load_config_normalizes_and_dedupes_github_orgs(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao", " Inkvi ", "polymerdao", "INKVI", ""]\n',
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.github_orgs == ["polymerdao", "Inkvi"]
    assert cfg.github_owners == ["polymerdao", "Inkvi"]


def test_load_config_requires_github_owner_scope(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('excluded_repos = ["infra"]\n', encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_legacy_github_org_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_org = "polymerdao"\ngithub_orgs=["Inkvi"]\n', encoding="utf-8")

    with pytest.raises(ValueError, match="github_org is no longer supported"):
        load_config(path)


def test_load_config_normalizes_excluded_repos(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'excluded_repos = [" polymerdao/infra ", "infra", "INFRA", "", "polymerdao/infra"]\n',
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.excluded_repos == ["polymerdao/infra", "infra"]


def test_load_config_normalizes_enabled_reviewers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'enabled_reviewers = [" codex ", "claude", "CODEX", ""]\n',
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.enabled_reviewers == ["codex", "claude"]


def test_load_config_rejects_invalid_enabled_reviewers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'enabled_reviewers = ["unknown"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_empty_enabled_reviewers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        "enabled_reviewers = []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_codex_backend(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'codex_backend = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_reconciler_backend(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'reconciler_backend = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_claude_reasoning_effort(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'claude_reasoning_effort = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_codex_reasoning_effort(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'codex_reasoning_effort = "max"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_reconciler_reasoning_effort(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'reconciler_reasoning_effort = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_reconciler_max_effort_for_codex_backend(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'reconciler_backend = "codex"\n'
        'reconciler_reasoning_effort = "max"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_accepts_gemini_reviewer(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'enabled_reviewers = ["gemini"]\n',
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.enabled_reviewers == ["gemini"]


def test_load_config_accepts_all_three_reviewers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'enabled_reviewers = ["claude", "codex", "gemini"]\n',
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.enabled_reviewers == ["claude", "codex", "gemini"]


def test_load_config_rejects_empty_gemini_model(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'gemini_model = ""\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_empty_reconciler_model(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'reconciler_model = ""\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_accepts_rerequest_or_commit_trigger_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'trigger_mode = "rerequest_or_commit"\n',
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.trigger_mode == "rerequest_or_commit"


def test_load_config_slash_command_enabled_defaults_true(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["Inkvi"]\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.slash_command_enabled is True


def test_load_config_slash_command_enabled_set_false(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["Inkvi"]\nslash_command_enabled = false\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.slash_command_enabled is False


def test_load_config_triage_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["Inkvi"]\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.triage_backend == "gemini"
    assert cfg.triage_model is None
    assert cfg.triage_timeout_seconds == 60


def test_load_config_lightweight_review_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["Inkvi"]\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.lightweight_review_backend == "claude"
    assert cfg.lightweight_review_model is None
    assert cfg.lightweight_review_reasoning_effort is None
    assert cfg.lightweight_review_timeout_seconds == 300


def test_load_config_rejects_invalid_triage_backend(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\ntriage_backend = "invalid"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_lightweight_review_backend(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nlightweight_review_backend = "invalid"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_lightweight_review_reasoning_effort(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nlightweight_review_reasoning_effort = "invalid"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_empty_triage_model(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\ntriage_model = ""\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_empty_lightweight_review_model(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nlightweight_review_model = ""\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_trigger_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'trigger_mode = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)
