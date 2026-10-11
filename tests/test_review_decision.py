from code_reviewer.review_decision import approval_blocker, infer_review_decision, risk_blocker


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


def _check_run(name: str, status: str = "COMPLETED", conclusion: str | None = "SUCCESS") -> dict:
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def test_approval_blocker_allows_green_mergeable_pr() -> None:
    status = {
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [
            _check_run("tests"),
            _check_run("lint", conclusion="SKIPPED"),
            {"__typename": "StatusContext", "context": "deploy", "state": "SUCCESS"},
        ],
    }
    assert approval_blocker(status) is None


def test_approval_blocker_allows_pr_without_checks() -> None:
    assert approval_blocker({"mergeable": "UNKNOWN", "statusCheckRollup": []}) is None


def test_approval_blocker_blocks_merge_conflicts() -> None:
    status = {"mergeable": "CONFLICTING", "statusCheckRollup": [_check_run("tests")]}
    assert approval_blocker(status) == "the PR has merge conflicts"


def test_approval_blocker_blocks_failed_checks() -> None:
    status = {
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [
            _check_run("tests", conclusion="FAILURE"),
            _check_run("build", status="IN_PROGRESS", conclusion=None),
            {"__typename": "StatusContext", "context": "deploy", "state": "ERROR"},
        ],
    }
    assert approval_blocker(status) == "checks failed: tests, deploy"


def test_approval_blocker_blocks_pending_checks() -> None:
    status = {
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [
            _check_run("build", status="QUEUED", conclusion=None),
            {"__typename": "StatusContext", "context": "deploy", "state": "PENDING"},
        ],
    }
    assert approval_blocker(status) == "checks still running: build, deploy"


def test_risk_blocker_allows_only_low_risk() -> None:
    assert risk_blocker("low", "docs only") is None
    assert risk_blocker("medium", "changes API behavior") == "medium risk (changes API behavior)"
    assert risk_blocker("high", None) == "high risk"
    assert risk_blocker(None, None) == "triage did not rate the risk"
