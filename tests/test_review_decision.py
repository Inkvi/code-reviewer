from code_reviewer.review_decision import (
    approval_blocker,
    first_high_risk_path,
    infer_review_decision,
    risk_blocker,
)


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
        "headRefOid": "abc",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [
            _check_run("tests"),
            _check_run("lint", conclusion="SKIPPED"),
            {"__typename": "StatusContext", "context": "deploy", "state": "SUCCESS"},
        ],
    }
    assert approval_blocker(status, "abc") is None


def test_approval_blocker_allows_pr_without_checks() -> None:
    assert (
        approval_blocker(
            {"headRefOid": "abc", "mergeable": "UNKNOWN", "statusCheckRollup": []}, "abc"
        )
        is None
    )


def test_approval_blocker_blocks_merge_conflicts() -> None:
    status = {
        "headRefOid": "abc",
        "mergeable": "CONFLICTING",
        "statusCheckRollup": [_check_run("tests")],
    }
    assert approval_blocker(status, "abc") == "the PR has merge conflicts"


def test_approval_blocker_blocks_failed_checks() -> None:
    status = {
        "headRefOid": "abc",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [
            _check_run("tests", conclusion="FAILURE"),
            _check_run("build", status="IN_PROGRESS", conclusion=None),
            {"__typename": "StatusContext", "context": "deploy", "state": "ERROR"},
        ],
    }
    assert approval_blocker(status, "abc") == "checks failed: tests, deploy"


def test_approval_blocker_blocks_pending_checks() -> None:
    status = {
        "headRefOid": "abc",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [
            _check_run("build", status="QUEUED", conclusion=None),
            {"__typename": "StatusContext", "context": "deploy", "state": "PENDING"},
        ],
    }
    assert approval_blocker(status, "abc") == "checks still running: build, deploy"


def test_risk_blocker_with_low_max_allows_only_low_risk() -> None:
    assert risk_blocker("low", "docs only", "low") is None
    assert (
        risk_blocker("medium", "changes API behavior", "low")
        == "medium risk (changes API behavior)"
    )
    assert risk_blocker("high", None, "low") == "high risk"
    assert risk_blocker(None, None, "low") == "triage did not rate the risk"


def test_risk_blocker_with_medium_max_allows_low_and_medium() -> None:
    assert risk_blocker("low", None, "medium") is None
    assert risk_blocker("medium", None, "medium") is None
    assert risk_blocker("high", "edits auth", "medium") == "high risk (edits auth)"
    assert risk_blocker(None, None, "medium") == "triage did not rate the risk"
    assert risk_blocker(None, "new commits arrived", "medium") == "new commits arrived"


def test_risk_blocker_with_high_max_skips_the_check() -> None:
    assert risk_blocker("high", "edits auth", "high") is None
    assert risk_blocker(None, None, "high") is None


def test_first_high_risk_path_matches_across_folders() -> None:
    paths = ["README.md", "src/auth/login.py", "src/billing/charge.py"]
    assert first_high_risk_path(paths, ["*auth*", "*billing*"]) == "src/auth/login.py"
    assert first_high_risk_path(["migrations/0042_drop.sql"], ["migrations/*"]) == (
        "migrations/0042_drop.sql"
    )


def test_first_high_risk_path_returns_none_without_match_or_patterns() -> None:
    assert first_high_risk_path(["docs/guide.md"], ["*auth*"]) is None
    assert first_high_risk_path(["src/auth/login.py"], []) is None
    assert first_high_risk_path(["src/Auth/login.py"], ["*auth*"]) is None, "case-sensitive"


def test_approval_blocker_blocks_when_pr_moved_past_reviewed_commit() -> None:
    status = {"headRefOid": "newer", "mergeable": "MERGEABLE", "statusCheckRollup": []}
    assert approval_blocker(status, "abc") == "new commits were pushed after the review"
    assert approval_blocker({"mergeable": "MERGEABLE"}, "abc") is not None, "missing head blocks"


def test_approval_blocker_blocks_closed_prs() -> None:
    status = {"state": "MERGED", "headRefOid": "abc", "mergeable": "UNKNOWN"}
    assert approval_blocker(status, "abc") == "the PR is no longer open"
