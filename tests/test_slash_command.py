from pr_reviewer.models import PRCandidate, SlashCommandTrigger


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
