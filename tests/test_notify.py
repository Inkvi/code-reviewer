import json

from code_reviewer import notify
from code_reviewer.models import PRCandidate
from code_reviewer.notify import post_to_slack, review_outcome_text


def _pr(title: str = "Fix login", risk: str | None = "low") -> PRCandidate:
    return PRCandidate(
        owner="o",
        repo="r",
        number=7,
        url="https://github.com/o/r/pull/7",
        title=title,
        author_login="alice",
        base_ref="main",
        head_sha="abc",
        updated_at="",
        risk=risk,
    )


def test_approved_message() -> None:
    assert review_outcome_text(_pr(), "approved", None) == (
        "*<https://github.com/o/r/pull/7|o/r#7>* Fix login\n:white_check_mark: Approved. Risk: low."
    )


def test_blocked_message_names_the_reason() -> None:
    text = review_outcome_text(_pr(risk="medium"), "posted", "medium risk (changes API)")
    assert text.endswith(":eyes: Needs a human: medium risk (changes API). Risk: medium.")


def test_changes_requested_and_plain_comment_messages() -> None:
    assert ":x: Changes requested." in review_outcome_text(_pr(), "changes_requested", None)
    assert ":speech_balloon: Review posted." in review_outcome_text(_pr(risk=None), "posted", None)


def test_no_message_while_waiting_or_when_nothing_was_posted() -> None:
    assert review_outcome_text(_pr(), "waiting_for_checks", "checks still running: ci") is None
    assert review_outcome_text(_pr(), "submission_failed", None) is None
    assert review_outcome_text(_pr(), "generated", None) is None


def test_pr_text_cannot_add_slack_links_or_pings() -> None:
    text = review_outcome_text(_pr(title="<!channel> & <http://x|y>"), "posted", "changes <a>")
    assert "<!channel>" not in text
    assert "&lt;!channel&gt; &amp; &lt;http://x|y&gt;" in text
    assert "changes &lt;a&gt;" in text


def test_post_to_slack_sends_json(monkeypatch) -> None:
    sent = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        sent["url"] = request.full_url
        sent["body"] = json.loads(request.data)
        sent["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    post_to_slack("https://hooks.slack.com/services/x", "hello")

    assert sent == {
        "url": "https://hooks.slack.com/services/x",
        "body": {"text": "hello"},
        "timeout": 10,
    }
