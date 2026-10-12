from __future__ import annotations

import json
import urllib.request

from code_reviewer.models import PRCandidate


def _escape(text: str) -> str:
    """Escape the characters Slack treats as markup, so PR text cannot add links or pings."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def review_outcome_text(pr: PRCandidate, status: str, blocked_reason: str | None) -> str | None:
    """Slack message for a finished review, or None when there is nothing final to report."""
    if status == "approved":
        outcome = ":white_check_mark: Approved."
    elif status == "changes_requested":
        outcome = ":x: Changes requested."
    elif status == "posted" and blocked_reason:
        outcome = f":eyes: Needs a human: {_escape(blocked_reason)}."
    elif status == "posted":
        outcome = ":speech_balloon: Review posted."
    else:
        return None
    risk = f" Risk: {pr.risk}." if pr.risk else ""
    return f"*<{pr.url}|{pr.key}>* {_escape(pr.title)}\n{outcome}{risk}"


def post_to_slack(webhook_url: str, text: str) -> None:
    """Send a message through a Slack incoming webhook."""
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10):  # noqa: S310
        pass
