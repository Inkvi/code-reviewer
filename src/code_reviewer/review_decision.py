from __future__ import annotations

import re
from fnmatch import fnmatchcase
from typing import Literal

SEVERE_FINDING_PATTERN = re.compile(r"\[(P0|P1)\]", re.IGNORECASE)


ReviewDecision = Literal["approve", "request_changes"]


def infer_review_decision(final_review: str) -> ReviewDecision:
    """Infer review decision from reconciled markdown findings.

    Rule: request changes if any P0/P1 finding exists; otherwise approve.
    """
    if SEVERE_FINDING_PATTERN.search(final_review):
        return "request_changes"
    return "approve"


_PASSING_CHECK_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def approval_blocker(pr_status: dict) -> str | None:
    """Return why a PR must not be auto-approved, or None if it can be.

    *pr_status* is ``gh pr view --json mergeable,statusCheckRollup`` output.
    Blocks on merge conflicts and on any check that failed or has not finished.
    """
    if pr_status.get("mergeable") == "CONFLICTING":
        return "the PR has merge conflicts"

    failing: list[str] = []
    pending: list[str] = []
    for check in pr_status.get("statusCheckRollup") or []:
        if check.get("__typename") == "StatusContext":
            name = check.get("context") or "status"
            state = check.get("state")
            if state in ("PENDING", "EXPECTED"):
                pending.append(name)
            elif state != "SUCCESS":
                failing.append(name)
        else:
            name = check.get("name") or "check"
            if check.get("status") != "COMPLETED":
                pending.append(name)
            elif check.get("conclusion") not in _PASSING_CHECK_CONCLUSIONS:
                failing.append(name)

    if failing:
        return f"checks failed: {', '.join(failing)}"
    if pending:
        return f"checks still running: {', '.join(pending)}"
    return None


_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def risk_blocker(risk: str | None, reason: str | None, max_risk: str) -> str | None:
    """Return why the triage risk level rules out auto-approval, or None if it is allowed.

    *max_risk* is the highest level that may be auto-approved. "high" turns the check off,
    so even an unrated PR is allowed.
    """
    if max_risk == "high":
        return None
    if risk is None:
        return "triage did not rate the risk"
    if _RISK_ORDER[risk] <= _RISK_ORDER[max_risk]:
        return None
    return f"{risk} risk ({reason})" if reason else f"{risk} risk"


def first_high_risk_path(paths: list[str], patterns: list[str]) -> str | None:
    """Return the first changed file that matches a high_risk_paths pattern, if any.

    Patterns use fnmatch rules, where ``*`` also matches across folders.
    """
    for path in paths:
        if any(fnmatchcase(path, pattern) for pattern in patterns):
            return path
    return None
