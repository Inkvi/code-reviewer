from __future__ import annotations

from typing import TYPE_CHECKING

from code_reviewer.logger import warn

if TYPE_CHECKING:
    from code_reviewer.github import GitHubClient
    from code_reviewer.models import PRCandidate


class _Stage:
    __slots__ = ("name", "state", "detail")

    def __init__(self, name: str, state: str = "pending", detail: str = "") -> None:
        self.name = name
        self.state = state  # pending | running | done | failed | skipped
        self.detail = detail


_MAX_REASON_LEN = 120

_ERROR_CLASS_RE = __import__("re").compile(r"\w+Error: .+")


def _truncate(text: str) -> str:
    """Extract the most informative part of an error for table display."""
    first_line = text.split("\n", 1)[0].strip()
    # Prefer a recognized error class near the end (e.g., "TerminalQuotaError: ...")
    match = _ERROR_CLASS_RE.search(first_line)
    if match:
        first_line = match.group(0)
    if len(first_line) > _MAX_REASON_LEN:
        return first_line[:_MAX_REASON_LEN] + "…"
    return first_line


_ICONS = {
    "pending": "⬜",
    "running": "⏳",
    "done": "✅",
    "failed": "❌",
    "skipped": "⊘",
}


class ProgressComment:
    def __init__(self, client: GitHubClient, pr: PRCandidate) -> None:
        self._client = client
        self._pr = pr
        self._node_id: str | None = None
        self._review_type: str | None = None
        # Start with just triage running
        self._stages: list[_Stage] = [_Stage("Triage", "running")]

    def render(self) -> str:
        label = self._review_type or "starting"
        lines = [
            f"**Review in progress** ({label})",
            "",
            "| Stage | Status |",
            "|-------|--------|",
        ]
        for s in self._stages:
            icon = _ICONS.get(s.state, "⬜")
            status = s.detail if s.detail else s.state.capitalize()
            lines.append(f"| {s.name} | {icon} {status} |")
        return "\n".join(lines)

    # -- State transitions (sync, no GitHub calls) --

    def set_triage_done(self, result: str, *, enabled_reviewers: list[str] | None = None) -> None:
        self._stages[0].state = "done"
        if result == "lightweight":
            self._review_type = "lightweight"
            self._stages[0].detail = "Lightweight"
            self._stages.append(_Stage("Review", "pending", "Pending"))
        else:
            self._review_type = "full review"
            self._stages[0].detail = "Full review"
            for name in enabled_reviewers or []:
                self._stages.append(_Stage(name.capitalize(), "pending", "Pending"))
            self._stages.append(_Stage("Reconciliation", "pending", "Pending"))

    def set_reviewer_started(self, name: str) -> None:
        stage = self._find(name.capitalize())
        if stage:
            stage.state = "running"
            stage.detail = "Running…"

    def set_reviewer_done(self, name: str, duration_s: float) -> None:
        stage = self._find(name.capitalize())
        if stage:
            stage.state = "done"
            stage.detail = f"Done ({duration_s:.0f}s)"

    def set_reviewer_failed(self, name: str, reason: str = "") -> None:
        stage = self._find(name.capitalize())
        if stage:
            stage.state = "failed"
            stage.detail = f"Failed: {_truncate(reason)}" if reason else "Failed"

    def set_reviewer_skipped(self, name: str, reason: str = "") -> None:
        stage = self._find(name.capitalize())
        if stage:
            stage.state = "skipped"
            stage.detail = f"Skipped: {_truncate(reason)}" if reason else "Skipped"

    def set_review_started(self) -> None:
        stage = self._find("Review")
        if stage:
            stage.state = "running"
            stage.detail = "Running…"

    def set_review_done(self, duration_s: float) -> None:
        stage = self._find("Review")
        if stage:
            stage.state = "done"
            stage.detail = f"Done ({duration_s:.0f}s)"

    def set_reconciliation_started(self) -> None:
        stage = self._find("Reconciliation")
        if stage:
            stage.state = "running"
            stage.detail = "Running…"

    def set_reconciliation_done(self, duration_s: float) -> None:
        stage = self._find("Reconciliation")
        if stage:
            stage.state = "done"
            stage.detail = f"Done ({duration_s:.0f}s)"

    def set_reconciliation_skipped(self) -> None:
        stage = self._find("Reconciliation")
        if stage:
            stage.state = "skipped"
            stage.detail = "Skipped"

    # -- GitHub operations (async) --

    async def create(self) -> None:
        """Post the initial progress comment. Safe to call — catches errors."""
        if self._pr.is_local:
            return
        try:
            import asyncio

            self._node_id = await asyncio.to_thread(
                self._client.create_pr_comment, self._pr, self.render()
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"{self._pr.key}: failed to create progress comment: {exc}")

    async def update(self) -> None:
        """Edit the progress comment. No-op if create() failed or local PR."""
        if self._node_id is None:
            return
        try:
            import asyncio

            await asyncio.to_thread(self._client.edit_pr_comment, self._node_id, self.render())
        except Exception as exc:  # noqa: BLE001
            warn(f"{self._pr.key}: failed to update progress comment: {exc}")

    # -- Helpers --

    def _find(self, name: str) -> _Stage | None:
        for s in self._stages:
            if s.name == name:
                return s
        return None
