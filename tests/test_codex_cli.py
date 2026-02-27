from pr_reviewer.reviewers.codex_cli import _extract_codex_review_text, _sanitize_codex_markdown


def test_extract_codex_review_from_stdout() -> None:
    out = "### Findings\n- [P2] file.rs:10 - issue"
    err = "logs"
    assert _extract_codex_review_text(out, err) == out


def test_extract_codex_review_from_stderr_codex_marker() -> None:
    err = "line1\nline2\ncodex\n### Findings\n- No material findings."
    assert _extract_codex_review_text("", err) == "### Findings\n- No material findings."


def test_extract_codex_review_from_stderr_assistant_marker() -> None:
    err = "foo\nassistant\n### Findings\n- [P3] src/lib.rs:1 - nit."
    assert _extract_codex_review_text("", err) == "### Findings\n- [P3] src/lib.rs:1 - nit."


def test_sanitize_codex_markdown_removes_internal_warnings() -> None:
    text = (
        "I found one issue.\n"
        "Failed to write last message file \"/tmp/x\": No such file or directory (os error 2)\n"
        "Warning: no last agent message; wrote empty content to /tmp/x\n"
    )
    assert _sanitize_codex_markdown(text) == "I found one issue."
