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
    assert cfg.excluded_repos == []


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
