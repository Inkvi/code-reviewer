from pathlib import Path

import pytest

from pr_reviewer.config import load_config


def test_load_config_success(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_org = "Inkvi"\n', encoding="utf-8")

    cfg = load_config(path)

    assert cfg.github_org == "Inkvi"
    assert cfg.poll_interval_seconds == 60
    assert cfg.auto_post_review is False
    assert cfg.auto_submit_review_decision is False
    assert cfg.include_reviewer_stderr is True
    assert cfg.excluded_repos == []
    assert cfg.enabled_reviewers == ["claude", "codex"]
    assert cfg.claude_model is None
    assert cfg.claude_reasoning_effort is None
    assert cfg.codex_backend == "cli"
    assert cfg.codex_model == "gpt-5.3-codex"
    assert cfg.codex_reasoning_effort == "low"


def test_load_config_invalid_interval(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_org = "Inkvi"\npoll_interval_seconds = 1\n', encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_normalizes_excluded_repos(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_org = "polymerdao"\n'
        'excluded_repos = [" polymerdao/infra ", "infra", "INFRA", "", "polymerdao/infra"]\n',
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.excluded_repos == ["polymerdao/infra", "infra"]


def test_load_config_normalizes_enabled_reviewers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_org = "polymerdao"\n'
        'enabled_reviewers = [" codex ", "claude", "CODEX", ""]\n',
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.enabled_reviewers == ["codex", "claude"]


def test_load_config_rejects_invalid_enabled_reviewers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_org = "polymerdao"\n'
        'enabled_reviewers = ["unknown"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_empty_enabled_reviewers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_org = "polymerdao"\n'
        "enabled_reviewers = []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_codex_backend(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_org = "polymerdao"\n'
        'codex_backend = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_claude_reasoning_effort(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_org = "polymerdao"\n'
        'claude_reasoning_effort = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_codex_reasoning_effort(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_org = "polymerdao"\n'
        'codex_reasoning_effort = "max"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)
