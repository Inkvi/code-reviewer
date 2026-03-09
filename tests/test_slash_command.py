import asyncio
from datetime import UTC, datetime
from pathlib import Path

from code_reviewer.config import AppConfig
from code_reviewer.github import GitHubClient
from code_reviewer.models import PRCandidate, ProcessedState, ReviewerOutput, SlashCommandTrigger
from code_reviewer.processor import process_candidate
from code_reviewer.reviewers.triage import TriageResult


def test_slash_command_trigger_defaults() -> None:
    trigger = SlashCommandTrigger(
        comment_id=123456,
        comment_author="alice",
        comment_created_at="2026-03-05T10:00:00+00:00",
        force=False,
    )
    assert trigger.comment_id == 123456
    assert trigger.force is False


def test_pr_candidate_slash_command_trigger_default_none() -> None:
    pr = PRCandidate(
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
    assert pr.slash_command_trigger is None


def test_pr_candidate_with_slash_command_trigger() -> None:
    trigger = SlashCommandTrigger(
        comment_id=123456,
        comment_author="alice",
        comment_created_at="2026-03-05T10:00:00+00:00",
        force=True,
    )
    pr = PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
        slash_command_trigger=trigger,
    )
    assert pr.slash_command_trigger is not None
    assert pr.slash_command_trigger.force is True


class DummyStore:
    def __init__(self, state: ProcessedState | None = None) -> None:
        self.state = state or ProcessedState()
        self.saved = False

    def get(self, _key):
        return self.state

    def set(self, _key, state):
        self.state = state

    def save(self) -> None:
        self.saved = True


class DummyWorkspace:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    def prepare(self, _pr):
        return self.workdir

    def update_to_latest(self, _workdir, pr):
        pass

    def cleanup(self, _workdir):
        return None


def _sample_pr_with_slash_command(*, force: bool = False) -> PRCandidate:
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
        additions=20,
        deletions=5,
        changed_file_paths=["src/app.py"],
        slash_command_trigger=SlashCommandTrigger(
            comment_id=123456,
            comment_author="alice",
            comment_created_at="2026-03-05T10:05:00+00:00",
            force=force,
        ),
    )


def _mock_triage_full_review(monkeypatch) -> None:
    async def fake_triage(*args, **kwargs):
        return TriageResult.FULL_REVIEW
    monkeypatch.setattr("code_reviewer.processor.run_triage", fake_triage)


def test_slash_command_triggers_review(monkeypatch, tmp_path) -> None:
    _mock_triage_full_review(monkeypatch)
    store = DummyStore()
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    reactions: list[tuple[str, str, int, str]] = []
    monkeypatch.setattr(
        GitHubClient,
        "add_reaction_to_comment",
        lambda _self, owner, repo, cid, reaction: reactions.append((owner, repo, cid, reaction)),
    )
    posted_comments: list[str] = []
    monkeypatch.setattr(
        GitHubClient,
        "post_pr_comment_inline",
        lambda _self, _pr, body: posted_comments.append(body),
    )

    now = datetime.now(UTC)
    ok_output = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="### Findings\n- No findings.\n\n### Test Gaps\n- None.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    async def fake_codex(_pr, _workdir, _timeout, *, model=None, reasoning_effort=None):
        return ok_output

    monkeypatch.setattr("code_reviewer.processor.run_codex_review", fake_codex)
    monkeypatch.setattr(
        "code_reviewer.processor.write_review_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.md",
    )
    monkeypatch.setattr(
        "code_reviewer.processor.write_reviewer_sidecar_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.raw.md",
    )

    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    result = asyncio.run(
        process_candidate(cfg, client, store, workspace, _sample_pr_with_slash_command())
    )

    assert result.processed is True
    assert store.state.last_slash_command_id == 123456
    assert ("polymerdao", "obul", 123456, "eyes") in reactions
    assert not any("starting review" in c.lower() for c in posted_comments)


def test_slash_command_skips_when_already_reviewed_at_head(monkeypatch, tmp_path) -> None:
    store = DummyStore(
        ProcessedState(
            last_processed_at="2026-03-05T09:00:00+00:00",
            last_reviewed_head_sha="deadbeef",
            last_status="posted",
        )
    )
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    reactions: list[tuple] = []
    monkeypatch.setattr(
        GitHubClient,
        "add_reaction_to_comment",
        lambda _self, owner, repo, cid, reaction: reactions.append((owner, repo, cid, reaction)),
    )
    posted_comments: list[str] = []
    monkeypatch.setattr(
        GitHubClient,
        "post_pr_comment_inline",
        lambda _self, _pr, body: posted_comments.append(body),
    )

    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    result = asyncio.run(
        process_candidate(cfg, client, store, workspace, _sample_pr_with_slash_command(force=False))
    )

    assert result.processed is False
    assert any("already reviewed" in c.lower() for c in posted_comments)
    assert store.state.last_slash_command_id == 123456


def test_slash_command_force_reviews_even_when_already_reviewed(monkeypatch, tmp_path) -> None:
    _mock_triage_full_review(monkeypatch)
    store = DummyStore(
        ProcessedState(
            last_processed_at="2026-03-05T09:00:00+00:00",
            last_reviewed_head_sha="deadbeef",
            last_status="posted",
        )
    )
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    monkeypatch.setattr(
        GitHubClient,
        "add_reaction_to_comment",
        lambda _self, *_args: None,
    )
    monkeypatch.setattr(
        GitHubClient,
        "post_pr_comment_inline",
        lambda _self, _pr, _body: None,
    )

    now = datetime.now(UTC)
    ok_output = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="### Findings\n- No findings.\n\n### Test Gaps\n- None.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    async def fake_codex(_pr, _workdir, _timeout, *, model=None, reasoning_effort=None):
        return ok_output

    monkeypatch.setattr("code_reviewer.processor.run_codex_review", fake_codex)
    monkeypatch.setattr(
        "code_reviewer.processor.write_review_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.md",
    )
    monkeypatch.setattr(
        "code_reviewer.processor.write_reviewer_sidecar_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.raw.md",
    )

    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    result = asyncio.run(
        process_candidate(cfg, client, store, workspace, _sample_pr_with_slash_command(force=True))
    )

    assert result.processed is True
    assert store.state.last_status == "generated"
    assert store.state.last_slash_command_id == 123456


def test_slash_command_full_flow_react_review_post(monkeypatch, tmp_path) -> None:
    """Integration test: /review comment → react → run review → post → persist state."""
    _mock_triage_full_review(monkeypatch)
    store = DummyStore()
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    # Track all GitHub API interactions in order.
    api_calls: list[str] = []

    monkeypatch.setattr(
        GitHubClient,
        "add_reaction_to_comment",
        lambda _self, owner, repo, cid, reaction: api_calls.append(
            f"react:{owner}/{repo}#{cid}:{reaction}"
        ),
    )

    posted_comments: list[str] = []
    monkeypatch.setattr(
        GitHubClient,
        "post_pr_comment_inline",
        lambda _self, pr, body: (
            api_calls.append(f"comment:{pr.key}"),
            posted_comments.append(body),
        ),
    )

    posted_reviews: list[str] = []
    monkeypatch.setattr(
        GitHubClient,
        "post_pr_comment",
        lambda _self, pr, body_file: (
            api_calls.append(f"post_review:{pr.key}"),
            posted_reviews.append(body_file),
        ),
    )

    now = datetime.now(UTC)
    ok_output = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="### Findings\n- [P3] app.py:10 - Minor style.\n\n### Test Gaps\n- None.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    async def fake_codex(_pr, _workdir, _timeout, *, model=None, reasoning_effort=None):
        api_calls.append("run_reviewer:codex")
        return ok_output

    monkeypatch.setattr("code_reviewer.processor.run_codex_review", fake_codex)
    monkeypatch.setattr(
        "code_reviewer.processor.write_review_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.md",
    )
    monkeypatch.setattr(
        "code_reviewer.processor.write_reviewer_sidecar_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.raw.md",
    )

    cfg = AppConfig(
        github_orgs=["polymerdao"],
        enabled_reviewers=["codex"],
        auto_post_review=True,
    )
    pr = _sample_pr_with_slash_command()

    result = asyncio.run(process_candidate(cfg, client, store, workspace, pr))

    # Verify the full flow happened in order.
    assert result.processed is True
    assert api_calls[0] == "react:polymerdao/obul#123456:eyes"
    assert "run_reviewer:codex" in api_calls
    assert any(call.startswith("post_review:") for call in api_calls)

    # Verify state was persisted correctly.
    assert store.state.last_slash_command_id == 123456
    assert store.state.last_reviewed_head_sha == "deadbeef"
    assert store.state.last_status == "posted"
    assert store.state.last_processed_at is not None
