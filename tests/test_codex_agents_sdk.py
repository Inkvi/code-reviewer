from pathlib import Path

from code_reviewer.models import PRCandidate
from code_reviewer.reviewers.codex_agents_sdk import _run_agents_codex_review_sync


class _FakeResult:
    def __init__(self, final_output: str) -> None:
        self.final_output = final_output
        self.usage = None


class _FakeRunner:
    last_input: str | None = None

    @staticmethod
    def run_sync(agent, input):  # noqa: ANN001, A002
        _FakeRunner.last_input = input
        return _FakeResult("### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.")


class _FakeAgent:
    last_kwargs: dict[str, object] | None = None

    def __init__(self, **kwargs):  # noqa: ANN003
        _FakeAgent.last_kwargs = kwargs


class _FakeOpenAIAgents:
    Agent = _FakeAgent
    Runner = _FakeRunner


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
    )


def test_agents_sdk_uses_system_prompt_as_instructions(monkeypatch, tmp_path: Path) -> None:
    prompt_path = tmp_path / "full.toml"
    prompt_path.write_text(
        'prompt = "Review {url}"\nsystem_prompt = "Use {workspace}"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "code_reviewer.reviewers.codex_agents_sdk._load_agents_sdk",
        lambda: _FakeOpenAIAgents,
    )

    markdown, token_usage = _run_agents_codex_review_sync(
        _sample_pr(),
        tmp_path,
        "gpt-5.3-codex",
        None,
        str(prompt_path),
    )

    assert "No material findings" in markdown
    assert token_usage is None
    assert _FakeAgent.last_kwargs is not None
    assert _FakeAgent.last_kwargs["instructions"] == f"Use {tmp_path}"
    assert _FakeRunner.last_input is not None
    assert "Review https://github.com/polymerdao/obul/pull/64" in _FakeRunner.last_input


def test_agents_sdk_omits_instructions_when_system_prompt_missing(
    monkeypatch, tmp_path: Path
) -> None:
    prompt_path = tmp_path / "full.toml"
    prompt_path.write_text(
        'prompt = "Review {url}"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "code_reviewer.reviewers.codex_agents_sdk._load_agents_sdk",
        lambda: _FakeOpenAIAgents,
    )

    _run_agents_codex_review_sync(
        _sample_pr(),
        tmp_path,
        "gpt-5.3-codex",
        None,
        str(prompt_path),
    )

    assert _FakeAgent.last_kwargs is not None
    assert "instructions" not in _FakeAgent.last_kwargs
