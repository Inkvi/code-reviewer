from code_reviewer.review_decision import infer_review_decision


def test_infer_review_decision_requests_changes_for_p0_or_p1() -> None:
    text = """
### Findings
- [P1] src/main.rs:10 - Something breaks.

### Test Gaps
- None noted.
""".strip()
    assert infer_review_decision(text) == "request_changes"


def test_infer_review_decision_approves_for_p2_p3_or_no_findings() -> None:
    text_p2 = """
### Findings
- [P2] src/lib.rs:4 - Minor issue.

### Test Gaps
- None noted.
""".strip()
    assert infer_review_decision(text_p2) == "approve"

    text_none = """
### Findings
- No material findings.

### Test Gaps
- None noted.
""".strip()
    assert infer_review_decision(text_none) == "approve"
