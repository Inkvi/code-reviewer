from pathlib import Path

import pytest

from code_reviewer.config import default_config, load_config


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
    assert cfg.reconciler_backend == ["claude"]
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


def test_load_config_allows_empty_github_orgs(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('excluded_repos = ["infra"]\n', encoding="utf-8")

    cfg = load_config(path)
    assert cfg.github_orgs == []
    assert cfg.excluded_repos == ["infra"]


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
        'github_orgs=["polymerdao"]\nenabled_reviewers = [" codex ", "claude", "CODEX", ""]\n',
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.enabled_reviewers == ["codex", "claude"]


def test_load_config_rejects_invalid_enabled_reviewers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nenabled_reviewers = ["unknown"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_empty_enabled_reviewers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nenabled_reviewers = []\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_codex_backend(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\ncodex_backend = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_reconciler_backend(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nreconciler_backend = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_claude_reasoning_effort(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nclaude_reasoning_effort = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_codex_reasoning_effort(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\ncodex_reasoning_effort = "max"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_invalid_reconciler_reasoning_effort(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nreconciler_reasoning_effort = "invalid"\n',
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
        'github_orgs=["polymerdao"]\nenabled_reviewers = ["gemini"]\n',
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.enabled_reviewers == ["gemini"]


def test_load_config_accepts_all_three_reviewers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nenabled_reviewers = ["claude", "codex", "gemini"]\n',
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg.enabled_reviewers == ["claude", "codex", "gemini"]


def test_load_config_rejects_empty_gemini_model(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\ngemini_model = ""\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_rejects_empty_reconciler_model(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\nreconciler_model = ""\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_accepts_rerequest_or_commit_trigger_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\ntrigger_mode = "rerequest_or_commit"\n',
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
    assert cfg.triage_backend == ["gemini"]
    assert cfg.triage_model == "gemini-3-flash-preview"
    assert cfg.triage_timeout_seconds == 60


def test_load_config_lightweight_review_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["Inkvi"]\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.lightweight_review_backend == ["gemini"]
    assert cfg.lightweight_review_model == "gemini-3-flash-preview"
    assert cfg.lightweight_review_reasoning_effort is None
    assert cfg.lightweight_review_timeout_seconds == 300


def test_load_config_resolves_relative_prompt_override_paths(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    prompt_path = prompt_dir / "triage.toml"
    prompt_path.write_text('prompt = "Review {url}"\n', encoding="utf-8")

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'github_orgs=["Inkvi"]\ntriage_prompt_path = "prompts/triage.toml"\n',
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.triage_prompt_path == str(prompt_path.resolve())


def test_load_config_rejects_empty_prompt_override_path(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["Inkvi"]\ntriage_prompt_path = "   "\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="prompt override path cannot be empty"):
        load_config(path)


def test_load_config_rejects_missing_prompt_override_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["Inkvi"]\ntriage_prompt_path = "missing.toml"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="prompt spec file not found"):
        load_config(path)


def test_load_config_rejects_invalid_prompt_override_toml(tmp_path: Path) -> None:
    prompt_path = tmp_path / "triage.toml"
    prompt_path.write_text('prompt = """oops"\n', encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'github_orgs=["Inkvi"]\ntriage_prompt_path = "{prompt_path.name}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid TOML"):
        load_config(config_path)


def test_load_config_rejects_prompt_override_without_prompt_field(tmp_path: Path) -> None:
    prompt_path = tmp_path / "triage.toml"
    prompt_path.write_text('system_prompt = "Only JSON"\n', encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'github_orgs=["Inkvi"]\ntriage_prompt_path = "{prompt_path.name}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="`prompt` is required"):
        load_config(config_path)


def test_load_config_rejects_prompt_override_unknown_keys(tmp_path: Path) -> None:
    prompt_path = tmp_path / "triage.toml"
    prompt_path.write_text(
        'prompt = "Review {url}"\nextra = "bad"\n',
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'github_orgs=["Inkvi"]\ntriage_prompt_path = "{prompt_path.name}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown prompt-spec keys"):
        load_config(config_path)


def test_load_config_rejects_unknown_prompt_placeholders(tmp_path: Path) -> None:
    prompt_path = tmp_path / "triage.toml"
    prompt_path.write_text('prompt = "Review {not_real}"\n', encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'github_orgs=["Inkvi"]\ntriage_prompt_path = "{prompt_path.name}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown placeholders"):
        load_config(config_path)


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


def test_load_config_rejects_codex_lightweight_max_reasoning_effort(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\n'
        'lightweight_review_backend = "codex"\n'
        'lightweight_review_reasoning_effort = "max"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="lightweight_review_reasoning_effort"):
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


def test_default_config_returns_valid_config() -> None:
    cfg = default_config()
    assert cfg.github_orgs == []
    assert cfg.enabled_reviewers == ["claude", "codex"]
    assert cfg.poll_interval_seconds == 60
    assert cfg.auto_post_review is False
    assert cfg.triage_backend == ["gemini"]
    assert cfg.lightweight_review_backend == ["gemini"]


def test_load_config_rejects_invalid_trigger_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["polymerdao"]\ntrigger_mode = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(path)


# --- Backend list syntax tests ---


def test_triage_backend_accepts_list(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["Inkvi"]\ntriage_backend = ["gemini", "claude"]\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.triage_backend == ["gemini", "claude"]


def test_triage_backend_string_normalized_to_list(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["Inkvi"]\ntriage_backend = "claude"\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.triage_backend == ["claude"]


def test_triage_backend_deduplicates(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["Inkvi"]\ntriage_backend = ["gemini", "GEMINI", "claude"]\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.triage_backend == ["gemini", "claude"]


def test_triage_backend_rejects_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["Inkvi"]\ntriage_backend = []\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_reconciler_backend_accepts_list(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["Inkvi"]\nreconciler_backend = ["claude", "gemini"]\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.reconciler_backend == ["claude", "gemini"]


def test_lightweight_review_backend_accepts_list(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["Inkvi"]\nlightweight_review_backend = ["gemini", "claude", "codex"]\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.lightweight_review_backend == ["gemini", "claude", "codex"]


def test_reconciler_rejects_max_effort_when_codex_in_chain(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["Inkvi"]\n'
        'reconciler_backend = ["claude", "codex"]\n'
        'reconciler_reasoning_effort = "max"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)


def test_lightweight_rejects_max_effort_when_codex_in_chain(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'github_orgs=["Inkvi"]\n'
        'lightweight_review_backend = ["gemini", "codex"]\n'
        'lightweight_review_reasoning_effort = "max"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)
