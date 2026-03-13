from datetime import UTC, datetime
from pathlib import Path

import pytest

from code_reviewer.models import PRCandidate, ReviewerOutput
from code_reviewer.prompts import (
    PromptBundle,
    PromptOverrideError,
    build_full_review_bundle,
    build_lightweight_bundle,
    build_reconcile_bundle,
    build_triage_bundle,
    get_default_prompt_spec_path,
    load_prompt_bundle,
    render_prompt_bundle,
)


def _sample_pr(*, is_local: bool = False, review_mode: str = "branch") -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64" if not is_local else "/tmp/repo",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
        additions=8,
        deletions=4,
        changed_file_paths=["src/app.py"],
        is_local=is_local,
        review_mode=review_mode,
    )


def _sample_output(name: str) -> ReviewerOutput:
    now = datetime.now(UTC)
    return ReviewerOutput(
        reviewer=name,
        status="ok",
        markdown="### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )


def test_default_triage_bundle_renders_expected_content(tmp_path: Path) -> None:
    bundle = build_triage_bundle(
        _sample_pr(),
        tmp_path,
        "\n<untrusted_data type='diff'>\n+new\n</untrusted_data>\n",
        None,
    )

    assert 'classify it as either "simple" or "full_review"' in bundle.prompt
    assert "<untrusted_data type='diff'>" in bundle.prompt
    assert bundle.system_prompt is not None


def test_default_prompt_specs_live_in_visible_files() -> None:
    triage_path = get_default_prompt_spec_path("triage")

    assert triage_path.name == "triage.toml"
    assert triage_path.exists()
    assert "classification" in triage_path.read_text(encoding="utf-8")


def test_default_lightweight_bundle_renders_expected_content(tmp_path: Path) -> None:
    bundle = build_lightweight_bundle(_sample_pr(), tmp_path, None)

    assert "Review checklist" in bundle.prompt
    assert bundle.system_prompt is not None


def test_default_full_review_bundle_renders_expected_content(tmp_path: Path) -> None:
    bundle = build_full_review_bundle(_sample_pr(), tmp_path, None)

    assert "actionable bugs" in bundle.prompt
    assert bundle.system_prompt is not None


def test_default_full_review_bundle_for_uncommitted_renders(
    tmp_path: Path,
) -> None:
    bundle = build_full_review_bundle(
        _sample_pr(is_local=True, review_mode="uncommitted"),
        tmp_path,
        None,
    )

    assert "actionable bugs" in bundle.prompt


def test_default_reconcile_bundle_renders_expected_content(tmp_path: Path) -> None:
    bundle = build_reconcile_bundle(
        _sample_pr(),
        tmp_path,
        [_sample_output("claude"), _sample_output("codex")],
        "- looks good",
        10,
        3,
        None,
    )

    assert "reconciling multiple code reviews" in bundle.prompt
    assert "Source A (Claude)" in bundle.prompt
    assert bundle.system_prompt is not None


def test_load_prompt_bundle_accepts_override_with_system_prompt(tmp_path: Path) -> None:
    path = tmp_path / "full.toml"
    path.write_text(
        'prompt = "Review {url}"\nsystem_prompt = "System {base_ref}"\n',
        encoding="utf-8",
    )

    bundle = load_prompt_bundle(path, step="full_review")
    rendered = render_prompt_bundle(
        bundle,
        step="full_review",
        values={
            "url_label": "URL",
            "url": "https://example.com",
            "title": "title",
            "base_ref": "main",
            "head_sha": "deadbeef",
            "changed_files": "src/app.py",
            "additions": 1,
            "deletions": 0,
            "workspace": str(tmp_path),
        },
    )

    assert rendered.prompt == "Review https://example.com"
    assert rendered.system_prompt == "System main"


def test_load_prompt_bundle_rejects_unknown_placeholders(tmp_path: Path) -> None:
    path = tmp_path / "triage.toml"
    path.write_text('prompt = "Review {bad}"\n', encoding="utf-8")

    with pytest.raises(PromptOverrideError, match="unknown placeholders"):
        load_prompt_bundle(path, step="triage")


def test_render_prompt_bundle_raises_when_required_value_missing() -> None:
    bundle = PromptBundle(prompt="Review {url}")

    with pytest.raises(PromptOverrideError, match="Missing placeholder value"):
        render_prompt_bundle(bundle, step="full_review", values={})
