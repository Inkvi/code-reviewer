from code_reviewer import logger


def test_log_messages_are_not_read_as_rich_markup(capsys) -> None:
    logger.info("risk reason: edits [/auth] permissions [bold]x[/bold]")

    out = capsys.readouterr().out
    assert "[/auth]" in out
    assert "[bold]x[/bold]" in out
