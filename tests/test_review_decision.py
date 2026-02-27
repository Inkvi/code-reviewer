from pr_reviewer.review_decision import infer_review_decision


def test_infer_review_decision_requests_changes_for_p1_or_p2() -> None:
    text = """
### Findings
- [P2] src/main.rs:10 - Something breaks.

### Test Gaps
- None noted.
""".strip()
    assert infer_review_decision(text) == "request_changes"


def test_infer_review_decision_approves_for_p3_or_no_findings() -> None:
    text_p3 = """
### Findings
- [P3] src/lib.rs:4 - Minor nit.

### Test Gaps
- None noted.
""".strip()
    assert infer_review_decision(text_p3) == "approve"

    text_none = """
### Findings
- No material findings.

### Test Gaps
- None noted.
""".strip()
    assert infer_review_decision(text_none) == "approve"
