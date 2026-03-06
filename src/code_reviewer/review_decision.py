from __future__ import annotations

import re
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
