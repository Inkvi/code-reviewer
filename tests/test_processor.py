import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pr_reviewer.config import AppConfig
from pr_reviewer.github import GitHubClient
from pr_reviewer.models import PRCandidate, ProcessedState, ReviewerOutput
from pr_reviewer.processor import (
    _compute_processing_decision,
    _single_reviewer_final_review,
    _start_codex_review_task,
    process_candidate,
)


def _sample_pr(
    *,
    latest_direct_rerequest_at: str | None = "2026-03-02T00:00:00+00:00",
    additions: int = 8,
    deletions: int = 4,
    changed_file_paths: list[str] | None = None,
) -> PRCandidate:
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
        latest_direct_rerequest_at=latest_direct_rerequest_at,
        additions=additions,
        deletions=deletions,
        changed_file_paths=changed_file_paths or ["src/app.py"],
    )


class DummyStore:
    def __init__(self, state: ProcessedState | None = None) -> None:
        self.state = state or ProcessedState()
        self.saved = False

    def get(self, _key):  # noqa: ANN001
        return self.state

    def set(self, _key, state):  # noqa: ANN001
        self.state = state

    def save(self) -> None:
        self.saved = True


class DummyWorkspace:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    def prepare(self, _pr):  # noqa: ANN001
        return self.workdir

    def cleanup(self, _workdir):  # noqa: ANN001
        return None


async def _ok_output(name: str) -> ReviewerOutput:
    now = datetime.now(UTC)
    return ReviewerOutput(
        reviewer=name,
        status="ok",
        markdown=f"{name} output",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )


def test_single_reviewer_final_review_uses_markdown_when_ok() -> None:
    now = datetime.now(UTC)
    output = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="### Findings\n- [P3] file.rs:1 - nit.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    final_review = _single_reviewer_final_review(output)
    assert "[P3]" in final_review


def test_single_reviewer_final_review_returns_failure_template() -> None:
    now = datetime.now(UTC)
    output = ReviewerOutput(
        reviewer="codex",
        status="error",
        markdown="",
        stdout="",
        stderr="failure",
        error="codex failed",
        started_at=now,
        ended_at=now,
    )

    final_review = _single_reviewer_final_review(output)
    assert "Reviewer failed" in final_review
    assert "codex failed" in final_review
    assert "### Test Gaps" in final_review


def test_start_codex_review_task_uses_cli_backend(monkeypatch) -> None:
    async def fake_codex_cli(  # noqa: ANN001
        pr, workdir, timeout_seconds, *, model=None, reasoning_effort=None
    ):
        assert pr.number == 64
        assert workdir == Path("/tmp/repo")
        assert timeout_seconds == 30
        assert model == "gpt-5.3-codex"
        assert reasoning_effort == "high"
        return await _ok_output("codex")

    async def fake_codex_agents(*_args, **_kwargs):  # noqa: ANN001
        raise AssertionError("agents backend should not be called")

    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", fake_codex_cli)
    monkeypatch.setattr("pr_reviewer.processor.run_codex_review_via_agents_sdk", fake_codex_agents)

    cfg = AppConfig(
        github_org="polymerdao",
        enabled_reviewers=["codex"],
        codex_backend="cli",
        codex_reasoning_effort="high",
        codex_timeout_seconds=30,
    )

    async def _run() -> ReviewerOutput:
        task = _start_codex_review_task(cfg, _sample_pr(), Path("/tmp/repo"))
        return await task

    output = asyncio.run(_run())

    assert output.status == "ok"
    assert output.markdown == "codex output"


def test_start_codex_review_task_uses_agents_backend(monkeypatch) -> None:
    async def fake_codex_cli(*_args, **_kwargs):  # noqa: ANN001
        raise AssertionError("cli backend should not be called")

    async def fake_codex_agents(  # noqa: ANN001
        pr, workdir, timeout_seconds, model, reasoning_effort=None
    ):
        assert pr.number == 64
        assert workdir == Path("/tmp/repo")
        assert timeout_seconds == 30
        assert model == "gpt-5.3-codex"
        assert reasoning_effort == "medium"
        return await _ok_output("codex")

    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", fake_codex_cli)
    monkeypatch.setattr("pr_reviewer.processor.run_codex_review_via_agents_sdk", fake_codex_agents)

    cfg = AppConfig(
        github_org="polymerdao",
        enabled_reviewers=["codex"],
        codex_backend="agents_sdk",
        codex_reasoning_effort="medium",
        codex_timeout_seconds=30,
    )

    async def _run() -> ReviewerOutput:
        task = _start_codex_review_task(cfg, _sample_pr(), Path("/tmp/repo"))
        return await task

    output = asyncio.run(_run())

    assert output.status == "ok"
    assert output.markdown == "codex output"


def test_compute_processing_decision_bootstrap_state() -> None:
    decision = _compute_processing_decision(
        ProcessedState(),
        _sample_pr(latest_direct_rerequest_at="2026-03-03T00:00:00+00:00"),
        trigger_mode="rerequest_only",
    )

    assert decision.should_process is True
    assert decision.reason == "bootstrap_missing_state"


def test_compute_processing_decision_missing_rerequest_data() -> None:
    decision = _compute_processing_decision(
        ProcessedState(
            last_processed_at="2026-03-03T00:00:00+00:00",
            last_seen_rerequest_at="2026-03-01T00:00:00+00:00",
        ),
        _sample_pr(latest_direct_rerequest_at=None),
        trigger_mode="rerequest_only",
    )

    assert decision.should_process is False
    assert decision.reason == "missing_rerequest_data"


def test_process_candidate_skips_small_change_set(monkeypatch, tmp_path) -> None:
    store = DummyStore()
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    monkeypatch.setattr(
        "pr_reviewer.processor.run_codex_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("reviewer should not run for small change set")
        ),
    )

    cfg = AppConfig(github_org="polymerdao", enabled_reviewers=["codex"])
    changed = asyncio.run(
        process_candidate(
            cfg,
            client,
            store,
            workspace,
            _sample_pr(additions=6, deletions=3, changed_file_paths=["src/app.py"]),
        )
    )

    assert changed is False
    assert store.saved is True
    assert store.state.last_status == "skipped_small_change_set"


def test_process_candidate_skips_config_only_files(monkeypatch, tmp_path) -> None:
    store = DummyStore()
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    monkeypatch.setattr(
        "pr_reviewer.processor.run_codex_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("reviewer should not run for config-only changes")
        ),
    )

    cfg = AppConfig(github_org="polymerdao", enabled_reviewers=["codex"])
    changed = asyncio.run(
        process_candidate(
            cfg,
            client,
            store,
            workspace,
            _sample_pr(
                additions=10,
                deletions=0,
                changed_file_paths=[".github/workflows/ci.yaml", "config/app.json"],
            ),
        )
    )

    assert changed is False
    assert store.saved is True
    assert store.state.last_status == "skipped_config_only_files"


def test_processes_on_bootstrap_when_state_missing(monkeypatch, tmp_path) -> None:
    store = DummyStore()
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    now = datetime.now(UTC)
    ok_output = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    async def fake_codex(_pr, _workdir, _timeout, *, model=None, reasoning_effort=None):  # noqa: ANN001
        return ok_output

    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", fake_codex)
    monkeypatch.setattr(
        "pr_reviewer.processor.write_review_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.md",
    )
    monkeypatch.setattr(
        "pr_reviewer.processor.write_reviewer_sidecar_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.raw.md",
    )

    cfg = AppConfig(github_org="polymerdao", enabled_reviewers=["codex"])
    changed = asyncio.run(process_candidate(cfg, client, store, workspace, _sample_pr()))

    assert changed is True
    assert store.saved is True
    assert store.state.last_status == "generated"
    assert store.state.last_processed_at is not None
    assert store.state.last_seen_rerequest_at == "2026-03-02T00:00:00+00:00"


def test_skips_without_new_rerequest_after_processed(monkeypatch, tmp_path) -> None:
    store = DummyStore(
        ProcessedState(
            last_processed_at="2026-03-03T00:00:00+00:00",
            last_seen_rerequest_at="2026-03-03T01:00:00+00:00",
        )
    )
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    monkeypatch.setattr(
        "pr_reviewer.processor.run_codex_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("reviewer should not run when no new trigger exists")
        ),
    )

    cfg = AppConfig(github_org="polymerdao", enabled_reviewers=["codex"])
    changed = asyncio.run(
        process_candidate(
            cfg,
            client,
            store,
            workspace,
            _sample_pr(latest_direct_rerequest_at="2026-03-03T01:00:00+00:00"),
        )
    )

    assert changed is False
    assert store.saved is True
    assert store.state.last_status == "skipped_no_new_trigger"


def test_processes_on_newer_direct_rerequest(monkeypatch, tmp_path) -> None:
    store = DummyStore(
        ProcessedState(
            last_processed_at="2026-03-03T00:00:00+00:00",
            last_seen_rerequest_at="2026-03-03T01:00:00+00:00",
        )
    )
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    now = datetime.now(UTC)
    ok_output = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    async def fake_codex(_pr, _workdir, _timeout, *, model=None, reasoning_effort=None):  # noqa: ANN001
        return ok_output

    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", fake_codex)
    monkeypatch.setattr(
        "pr_reviewer.processor.write_review_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.md",
    )
    monkeypatch.setattr(
        "pr_reviewer.processor.write_reviewer_sidecar_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.raw.md",
    )

    cfg = AppConfig(github_org="polymerdao", enabled_reviewers=["codex"])
    changed = asyncio.run(
        process_candidate(
            cfg,
            client,
            store,
            workspace,
            _sample_pr(latest_direct_rerequest_at="2026-03-03T02:00:00+00:00"),
        )
    )

    assert changed is True
    assert store.state.last_status == "generated"
    assert store.state.last_seen_rerequest_at == "2026-03-03T02:00:00+00:00"


def test_does_not_advance_trigger_checkpoint_on_failure(monkeypatch, tmp_path) -> None:
    store = DummyStore(
        ProcessedState(
            last_processed_at="2026-03-03T00:00:00+00:00",
            last_seen_rerequest_at="2026-03-03T01:00:00+00:00",
        )
    )
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    async def fake_codex(_pr, _workdir, _timeout, *, model=None, reasoning_effort=None):  # noqa: ANN001
        raise RuntimeError("codex boom")

    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", fake_codex)

    cfg = AppConfig(github_org="polymerdao", enabled_reviewers=["codex"])
    changed = asyncio.run(
        process_candidate(
            cfg,
            client,
            store,
            workspace,
            _sample_pr(latest_direct_rerequest_at="2026-03-03T02:00:00+00:00"),
        )
    )

    assert changed is False
    assert store.saved is True
    assert store.state.last_status == "error: codex boom"
    assert store.state.last_processed_at == "2026-03-03T00:00:00+00:00"
    assert store.state.last_seen_rerequest_at == "2026-03-03T01:00:00+00:00"


def test_use_saved_review_still_bypasses_generation(monkeypatch, tmp_path) -> None:
    store = DummyStore(
        ProcessedState(
            last_processed_at="2026-03-03T00:00:00+00:00",
            last_seen_rerequest_at="2026-03-03T00:00:00+00:00",
        )
    )
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    pr = _sample_pr(latest_direct_rerequest_at="2026-03-03T03:00:00+00:00")
    review_path = tmp_path / pr.owner / pr.repo / f"pr-{pr.number}.md"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text("### Findings\n- [P3] Existing review.\n", encoding="utf-8")

    posted: list[str] = []
    monkeypatch.setattr(
        GitHubClient,
        "post_pr_comment",
        lambda _self, _pr, body_file: posted.append(body_file),
    )
    monkeypatch.setattr(
        "pr_reviewer.processor.run_codex_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("reviewer should not run when using saved review")
        ),
    )

    cfg = AppConfig(
        github_org="polymerdao",
        enabled_reviewers=["codex"],
        output_dir=str(tmp_path),
        auto_post_review=True,
    )
    changed = asyncio.run(
        process_candidate(
            cfg,
            client,
            store,
            workspace,
            pr,
            use_saved_review=True,
        )
    )

    assert changed is True
    assert posted == [str(review_path)]
    assert store.state.last_status == "posted"
    assert store.state.last_output_file == str(review_path.resolve())


def test_saved_review_existing_does_not_skip_normal_flow(monkeypatch, tmp_path) -> None:
    store = DummyStore()
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")
    pr = _sample_pr()

    review_path = tmp_path / pr.owner / pr.repo / f"pr-{pr.number}.md"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text("old review", encoding="utf-8")

    now = datetime.now(UTC)
    ok_output = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="### Findings\n- No material findings.\n\n### Test Gaps\n- None noted.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    async def fake_codex(_pr, _workdir, _timeout, *, model=None, reasoning_effort=None):  # noqa: ANN001
        return ok_output

    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", fake_codex)
    monkeypatch.setattr(
        "pr_reviewer.processor.write_review_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.md",
    )
    monkeypatch.setattr(
        "pr_reviewer.processor.write_reviewer_sidecar_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.raw.md",
    )

    cfg = AppConfig(github_org="polymerdao", enabled_reviewers=["codex"], output_dir=str(tmp_path))
    changed = asyncio.run(process_candidate(cfg, client, store, workspace, pr))

    assert changed is True
    assert store.state.last_status == "generated"


def test_process_candidate_reconcile_uses_enabled_reviewer_order(monkeypatch, tmp_path) -> None:
    store = DummyStore()
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    now = datetime.now(UTC)
    codex_output = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="### Findings\n- [P3] a.py:1 - codex note.\n\n### Test Gaps\n- None noted.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )
    gemini_output = ReviewerOutput(
        reviewer="gemini",
        status="ok",
        markdown="### Findings\n- [P3] b.py:2 - gemini note.\n\n### Test Gaps\n- None noted.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    async def fake_codex(_pr, _workdir, _timeout, *, model=None, reasoning_effort=None):  # noqa: ANN001
        return codex_output

    async def fake_gemini(_pr, _workdir, _timeout, *, model=None):  # noqa: ANN001
        return gemini_output

    seen_order: list[str] = []
    seen_comments: list[str] = []

    async def fake_reconcile(  # noqa: ANN001
        _pr,
        _workdir,
        reviewer_outputs,
        _timeout,
        *,
        pr_comments=None,
        claude_model=None,
        claude_reasoning_effort=None,
    ):
        seen_order.extend(output.reviewer for output in reviewer_outputs)
        seen_comments.extend(pr_comments or [])
        return "### Findings\n- No material findings.\n\n### Test Gaps\n- None noted."

    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", fake_codex)
    monkeypatch.setattr("pr_reviewer.processor.run_gemini_review", fake_gemini)
    monkeypatch.setattr(
        GitHubClient,
        "get_pr_issue_comments",
        lambda _self, _pr: ["@alice (2026-03-03T00:00:00Z): please verify x"],
    )
    monkeypatch.setattr("pr_reviewer.processor.reconcile_reviews", fake_reconcile)
    monkeypatch.setattr(
        "pr_reviewer.processor.write_review_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.md",
    )
    monkeypatch.setattr(
        "pr_reviewer.processor.write_reviewer_sidecar_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.raw.md",
    )

    cfg = AppConfig(github_org="polymerdao", enabled_reviewers=["gemini", "codex"])
    changed = asyncio.run(process_candidate(cfg, client, store, workspace, _sample_pr()))

    assert changed is True
    assert seen_order == ["gemini", "codex"]
    assert seen_comments == ["@alice (2026-03-03T00:00:00Z): please verify x"]
