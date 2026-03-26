import asyncio

from code_reviewer.progress import ProgressComment


class FakeClient:
    """Stub client — render() is pure, no GitHub calls needed."""

    pass


class FakePR:
    is_local = False
    owner = "org"
    repo = "repo"
    number = 1
    url = "https://github.com/org/repo/pull/1"
    key = "org/repo#1"


# -- Render tests (pure, no GitHub calls) --


def test_render_initial_state():
    pc = ProgressComment(FakeClient(), FakePR())
    text = pc.render()
    assert "Review in progress" in text
    assert "Triage" in text
    assert "⏳" in text


def test_render_after_triage_lightweight():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("lightweight")
    text = pc.render()
    assert "lightweight" in text.lower()
    assert "Review" in text
    assert "Reconciliation" not in text


def test_render_after_triage_full():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("full", enabled_reviewers=["claude", "codex"])
    text = pc.render()
    assert "full review" in text.lower()
    assert "Claude" in text
    assert "Codex" in text
    assert "Gemini" not in text
    assert "Reconciliation" in text


def test_render_reviewer_done_with_duration():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("full", enabled_reviewers=["claude"])
    pc.set_reviewer_started("claude")
    pc.set_reviewer_done("claude", 42.3)
    text = pc.render()
    assert "✅" in text
    assert "42s" in text


def test_render_reviewer_failed():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("full", enabled_reviewers=["claude"])
    pc.set_reviewer_failed("claude")
    text = pc.render()
    assert "❌" in text
    assert "❌ Failed |" in text


def test_render_reviewer_failed_with_reason():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("full", enabled_reviewers=["claude"])
    pc.set_reviewer_failed("claude", "rate limit exceeded")
    text = pc.render()
    assert "❌" in text
    assert "Failed: rate limit exceeded" in text


def test_render_reviewer_skipped():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("full", enabled_reviewers=["claude"])
    pc.set_reviewer_skipped("claude")
    text = pc.render()
    assert "⊘" in text
    assert "⊘ Skipped |" in text


def test_render_reviewer_skipped_with_reason():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("full", enabled_reviewers=["claude"])
    pc.set_reviewer_skipped("claude", "quota exhausted on claude (resets in 2h30m)")
    text = pc.render()
    assert "⊘" in text
    assert "Skipped: quota exhausted on claude (resets in 2h30m)" in text


def test_render_reason_truncated():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("full", enabled_reviewers=["claude"])
    long_reason = "x" * 200
    pc.set_reviewer_failed("claude", long_reason)
    text = pc.render()
    assert "…" in text
    assert long_reason not in text


def test_render_reason_multiline_uses_first_line():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("full", enabled_reviewers=["claude"])
    pc.set_reviewer_failed("claude", "first line\nsecond line\nthird line")
    text = pc.render()
    assert "Failed: first line" in text
    assert "second line" not in text


def test_render_reason_extracts_error_class():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("full", enabled_reviewers=["gemini"])
    error = (
        "gemini exited with status 1: Error when talking to Gemini API "
        "Full report available at: /tmp/gemini-client-error.json "
        "TerminalQuotaError: You have exhausted your capacity. Resets after 17h59m42s."
    )
    pc.set_reviewer_failed("gemini", error)
    text = pc.render()
    assert "TerminalQuotaError:" in text
    assert "Resets after 17h59m42s" in text
    assert "/tmp/gemini-client-error.json" not in text


def test_render_reconciliation_skipped():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("full", enabled_reviewers=["claude", "codex"])
    pc.set_reconciliation_skipped()
    text = pc.render()
    assert "⊘" in text


def test_render_reconciliation_done():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("full", enabled_reviewers=["claude", "codex"])
    pc.set_reconciliation_started()
    pc.set_reconciliation_done(15.7)
    text = pc.render()
    assert "16s" in text  # rounded


def test_render_lightweight_review_done():
    pc = ProgressComment(FakeClient(), FakePR())
    pc.set_triage_done("lightweight")
    pc.set_review_started()
    pc.set_review_done(8.2)
    text = pc.render()
    assert "8s" in text


# -- Async lifecycle tests --


class MockClient:
    def __init__(self):
        self.created: list[tuple[str, str]] = []
        self.edited: list[tuple[str, str]] = []

    def create_pr_comment(self, pr, body):
        self.created.append((pr.key, body))
        return "IC_node123"

    def edit_pr_comment(self, node_id, body):
        self.edited.append((node_id, body))


def test_create_posts_comment():
    client = MockClient()
    pc = ProgressComment(client, FakePR())
    asyncio.run(pc.create())
    assert len(client.created) == 1
    assert "Review in progress" in client.created[0][1]


def test_update_edits_comment():
    client = MockClient()
    pc = ProgressComment(client, FakePR())
    asyncio.run(pc.create())
    pc.set_triage_done("full", enabled_reviewers=["claude"])
    asyncio.run(pc.update())
    assert len(client.edited) == 1
    assert "Full review" in client.edited[0][1]


def test_update_noop_without_create():
    client = MockClient()
    pc = ProgressComment(client, FakePR())
    asyncio.run(pc.update())
    assert client.edited == []


def test_create_noop_for_local_pr():
    client = MockClient()
    pr = FakePR()
    pr.is_local = True
    pc = ProgressComment(client, pr)
    asyncio.run(pc.create())
    assert client.created == []


def test_create_failure_degrades_gracefully():
    class FailingClient:
        def create_pr_comment(self, _pr, _body):
            raise RuntimeError("network error")

        def edit_pr_comment(self, _node_id, _body):
            raise AssertionError("should not be called")

    pc = ProgressComment(FailingClient(), FakePR())
    asyncio.run(pc.create())  # should not raise
    asyncio.run(pc.update())  # should not raise or call edit
