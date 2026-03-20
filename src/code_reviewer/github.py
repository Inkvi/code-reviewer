from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from code_reviewer.config import AppConfig
from code_reviewer.logger import warn
from code_reviewer.models import PRCandidate, SlashCommandTrigger
from code_reviewer.shell import run_command, run_json

if TYPE_CHECKING:
    from code_reviewer.state import StateStore

_REVIEW_CMD_RE = re.compile(r"^\s*/review(?:\s+(force))?\s*$", re.MULTILINE)


@dataclass(slots=True)
class GitHubClient:
    viewer_login: str
    _REREQUEST_EVENTS_JQ = (
        '.[] | select(.event == "review_requested" and .requested_reviewer.login != null) '
        "| [.requested_reviewer.login, .created_at] | @tsv"
    )

    @staticmethod
    def _parse_owner_repo_from_pr_url(pr_url: str) -> tuple[str, str]:
        parsed = urlparse(pr_url)
        if parsed.netloc.lower() != "github.com":
            raise ValueError(f"Unsupported PR URL host: {parsed.netloc or '<empty>'}")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 4 or parts[2] != "pull":
            raise ValueError(f"Invalid GitHub PR URL: {pr_url}")
        return parts[0], parts[1]

    @staticmethod
    def _is_repo_excluded(config: AppConfig, owner: str, repo: str) -> bool:
        if not config.excluded_repos:
            return False

        repo_name = repo.lower()
        full_name = f"{owner}/{repo}".lower()

        for excluded in config.excluded_repos:
            # Support either "owner/repo" or bare "repo".
            if "/" in excluded and excluded == full_name:
                return True
            if "/" not in excluded and excluded == repo_name:
                return True
        return False

    @staticmethod
    def _normalize_iso_timestamp(value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC).replace(microsecond=0).isoformat()

    def _latest_direct_rerequest_at(self, owner: str, repo: str, number: int) -> str | None:
        endpoint = f"repos/{owner}/{repo}/issues/{number}/events"
        proc = run_command(
            [
                "gh",
                "api",
                "--paginate",
                endpoint,
                "--jq",
                self._REREQUEST_EVENTS_JQ,
            ]
        )

        viewer_login = self.viewer_login.lower()
        latest: datetime | None = None
        for line in proc.stdout.splitlines():
            login, sep, created_at = line.partition("\t")
            if not sep:
                continue
            if login.strip().lower() != viewer_login:
                continue
            normalized = self._normalize_iso_timestamp(created_at)
            if normalized is None:
                continue
            parsed = datetime.fromisoformat(normalized)
            if latest is None or parsed > latest:
                latest = parsed
        return latest.isoformat() if latest is not None else None

    @staticmethod
    def _extract_changed_file_paths(details: object) -> list[str]:
        if not isinstance(details, dict):
            return []
        files = details.get("files")
        if not isinstance(files, list):
            return []
        paths: list[str] = []
        for entry in files:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if isinstance(path, str) and path.strip():
                paths.append(path.strip())
        return paths

    @staticmethod
    def _collapse_whitespace(text: str) -> str:
        return " ".join(text.split())

    def get_pr_issue_comments(
        self,
        pr: PRCandidate,
        *,
        max_comments: int = 20,
        per_comment_chars: int = 2000,
    ) -> list[str]:
        endpoint = f"repos/{pr.owner}/{pr.repo}/issues/{pr.number}/comments"
        proc = run_command(
            [
                "gh",
                "api",
                "--paginate",
                endpoint,
                "--jq",
                ".[] | [.user.login, .created_at, .body] | @json",
            ]
        )

        comments: list[str] = []
        for line in proc.stdout.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, list) or len(payload) != 3:
                continue
            login, created_at, body = payload
            if (
                not isinstance(login, str)
                or not isinstance(created_at, str)
                or not isinstance(body, str)
            ):
                continue

            condensed = self._collapse_whitespace(body).strip()
            if not condensed:
                continue
            if len(condensed) > per_comment_chars:
                condensed = f"{condensed[: per_comment_chars - 1]}…"
            comments.append(f"@{login} ({created_at}): {condensed}")

        if max_comments <= 0:
            return []
        return comments[-max_comments:]

    def discover_pr_candidates(self, config: AppConfig) -> list[PRCandidate]:
        candidates_by_key: dict[str, PRCandidate] = {}
        for owner_scope in config.github_owners:
            try:
                data = run_json(
                    [
                        "gh",
                        "search",
                        "prs",
                        "--owner",
                        owner_scope,
                        "--state",
                        "open",
                        "--review-requested",
                        "@me",
                        "--json",
                        "number,repository,url,title,author,isDraft,updatedAt",
                        "-L",
                        "200",
                    ]
                )
            except Exception as exc:  # noqa: BLE001
                warn(f"Failed to discover PRs for {owner_scope}: {exc}")
                continue

            if not isinstance(data, list):
                continue

            for item in data:
                if item.get("isDraft"):
                    continue

                author_login = (item.get("author") or {}).get("login", "")
                if config.skip_own_prs and author_login == self.viewer_login:
                    continue

                repo_full = item.get("repository", {}).get("nameWithOwner", "")
                if "/" not in repo_full:
                    continue
                owner, repo = repo_full.split("/", maxsplit=1)
                if self._is_repo_excluded(config, owner, repo):
                    continue

                details = run_json(
                    [
                        "gh",
                        "pr",
                        "view",
                        item["url"],
                        "--json",
                        "baseRefName,headRefOid,additions,deletions,files,body",
                    ]
                )
                latest_direct_rerequest_at = None
                try:
                    latest_direct_rerequest_at = self._latest_direct_rerequest_at(
                        owner, repo, int(item["number"])
                    )
                except Exception as exc:  # noqa: BLE001
                    warn(
                        f"{owner}/{repo}#{item['number']}: "
                        f"failed to fetch review-request events: {exc}"
                    )

                candidate = PRCandidate(
                    owner=owner,
                    repo=repo,
                    number=int(item["number"]),
                    url=item["url"],
                    title=item.get("title", ""),
                    author_login=author_login,
                    base_ref=details.get("baseRefName", "main"),
                    head_sha=details.get("headRefOid", ""),
                    updated_at=item.get("updatedAt", ""),
                    latest_direct_rerequest_at=latest_direct_rerequest_at,
                    description=details.get("body", "") or "",
                    additions=int(details.get("additions", 0) or 0),
                    deletions=int(details.get("deletions", 0) or 0),
                    changed_file_paths=self._extract_changed_file_paths(details),
                )

                key = candidate.key.lower()
                existing = candidates_by_key.get(key)
                if existing is None or candidate.updated_at > existing.updated_at:
                    candidates_by_key[key] = candidate

        candidates = list(candidates_by_key.values())
        candidates.sort(key=lambda pr: pr.updated_at)
        return candidates

    def get_pr_candidate(self, pr_url: str) -> PRCandidate:
        owner, repo = self._parse_owner_repo_from_pr_url(pr_url)
        details = run_json(
            [
                "gh",
                "pr",
                "view",
                pr_url,
                "--json",
                "number,url,title,author,baseRefName,headRefOid,updatedAt,additions,deletions,files,body",
            ]
        )
        author = details.get("author") or {}
        latest_direct_rerequest_at = None
        try:
            latest_direct_rerequest_at = self._latest_direct_rerequest_at(
                owner, repo, int(details["number"])
            )
        except Exception as exc:  # noqa: BLE001
            warn(
                f"{owner}/{repo}#{details['number']}: failed to fetch review-request events: {exc}"
            )

        return PRCandidate(
            owner=owner,
            repo=repo,
            number=int(details["number"]),
            url=details["url"],
            title=details.get("title", ""),
            author_login=author.get("login", ""),
            base_ref=details.get("baseRefName", "main"),
            head_sha=details.get("headRefOid", ""),
            updated_at=details.get("updatedAt", ""),
            latest_direct_rerequest_at=latest_direct_rerequest_at,
            description=details.get("body", "") or "",
            additions=int(details.get("additions", 0) or 0),
            deletions=int(details.get("deletions", 0) or 0),
            changed_file_paths=self._extract_changed_file_paths(details),
        )

    @staticmethod
    def get_pr_head_sha(pr: PRCandidate) -> str:
        """Fetch the current head SHA of a PR via a lightweight API call."""
        details = run_json(
            [
                "gh",
                "pr",
                "view",
                pr.url,
                "--json",
                "headRefOid",
            ]
        )
        return details.get("headRefOid", "")

    def has_issue_comment_by_viewer(self, pr: PRCandidate) -> bool:
        endpoint = f"repos/{pr.owner}/{pr.repo}/issues/{pr.number}/comments"
        proc = run_command(
            [
                "gh",
                "api",
                "--paginate",
                endpoint,
                "--jq",
                ".[] | .user.login",
            ]
        )
        return any(line.strip() == self.viewer_login for line in proc.stdout.splitlines())

    def add_eyes_reaction(self, pr: PRCandidate) -> None:
        run_command(
            [
                "gh",
                "api",
                f"repos/{pr.owner}/{pr.repo}/issues/{pr.number}/reactions",
                "-f",
                "content=eyes",
                "--silent",
            ]
        )

    @staticmethod
    def check_org_membership(org: str, login: str) -> bool:
        try:
            run_command(["gh", "api", f"orgs/{org}/members/{login}", "--silent"])
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def add_reaction_to_comment(owner: str, repo: str, comment_id: int, reaction: str) -> None:
        run_command(
            [
                "gh",
                "api",
                f"repos/{owner}/{repo}/issues/comments/{comment_id}/reactions",
                "-f",
                f"content={reaction}",
                "--silent",
            ]
        )

    def _is_slash_command_authorized(self, org: str, login: str, pr_author: str) -> bool:
        if login.lower() == pr_author.lower():
            return True
        return self.check_org_membership(org, login)

    def _find_latest_review_command(
        self,
        owner: str,
        repo: str,
        number: int,
        pr_author: str,
        last_processed_command_id: int | None,
    ) -> SlashCommandTrigger | None:
        proc = run_command(
            [
                "gh",
                "api",
                "--paginate",
                f"repos/{owner}/{repo}/issues/{number}/comments",
                "--jq",
                ".[] | {id, user: .user.login, created_at, body} | @json",
            ],
            retries=2,
        )

        latest: SlashCommandTrigger | None = None
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                comment = json.loads(line)
            except json.JSONDecodeError:
                continue

            comment_id = comment.get("id")
            if not isinstance(comment_id, int):
                continue
            if last_processed_command_id is not None and comment_id <= last_processed_command_id:
                continue

            body = comment.get("body", "")
            match = _REVIEW_CMD_RE.search(body)
            if not match:
                continue

            author = comment.get("user", "")
            if not self._is_slash_command_authorized(owner, author, pr_author):
                continue

            force = match.group(1) is not None
            latest = SlashCommandTrigger(
                comment_id=comment_id,
                comment_author=author,
                comment_created_at=comment.get("created_at", ""),
                force=force,
            )

        return latest

    def discover_slash_command_candidates(
        self, config: AppConfig, store: StateStore
    ) -> list[PRCandidate]:
        if not config.slash_command_enabled:
            return []

        candidates: list[PRCandidate] = []
        for owner in config.github_owners:
            try:
                data = run_json(
                    [
                        "gh",
                        "search",
                        "prs",
                        "--owner",
                        owner,
                        "--state",
                        "open",
                        "--json",
                        "number,repository,url,title,author,updatedAt",
                        "-L",
                        "200",
                        "--sort",
                        "updated",
                    ]
                )
            except Exception as exc:  # noqa: BLE001
                warn(f"Failed to search slash commands for {owner}: {exc}")
                continue

            if not isinstance(data, list):
                continue

            for item in data:
                repo_full = item.get("repository", {}).get("nameWithOwner", "")
                if "/" not in repo_full:
                    continue
                item_owner, repo = repo_full.split("/", maxsplit=1)
                if self._is_repo_excluded(config, item_owner, repo):
                    continue

                number = int(item["number"])
                pr_author = (item.get("author") or {}).get("login", "")
                pr_key = f"{item_owner}/{repo}#{number}"
                state = store.get(pr_key)

                trigger = self._find_latest_review_command(
                    item_owner, repo, number, pr_author, state.last_slash_command_id
                )
                if trigger is None:
                    continue

                details = run_json(
                    [
                        "gh",
                        "pr",
                        "view",
                        item["url"],
                        "--json",
                        "number,url,title,author,baseRefName,headRefOid,updatedAt,additions,deletions,files,body",
                    ]
                )
                det_author = (details.get("author") or {}).get("login", "")
                candidate = PRCandidate(
                    owner=item_owner,
                    repo=repo,
                    number=number,
                    url=details.get("url", item["url"]),
                    title=details.get("title", item.get("title", "")),
                    author_login=det_author or pr_author,
                    base_ref=details.get("baseRefName", "main"),
                    head_sha=details.get("headRefOid", ""),
                    updated_at=details.get("updatedAt", item.get("updatedAt", "")),
                    description=details.get("body", "") or "",
                    additions=int(details.get("additions", 0) or 0),
                    deletions=int(details.get("deletions", 0) or 0),
                    changed_file_paths=self._extract_changed_file_paths(details),
                    slash_command_trigger=trigger,
                )
                candidates.append(candidate)

        return candidates

    def post_pr_comment(self, pr: PRCandidate, body_file: str) -> None:
        run_command(["gh", "pr", "comment", pr.url, "--body-file", body_file])

    def post_pr_comment_inline(self, pr: PRCandidate, body: str) -> None:
        run_command(["gh", "pr", "comment", pr.url, "--body", body])

    def create_pr_comment(self, pr: PRCandidate, body: str) -> str:
        """Post a PR comment and return its GraphQL node ID."""
        result = run_json(
            [
                "gh",
                "api",
                f"repos/{pr.owner}/{pr.repo}/issues/{pr.number}/comments",
                "-f",
                f"body={body}",
            ]
        )
        return result["node_id"]

    def edit_pr_comment(self, node_id: str, body: str) -> None:
        """Edit an existing PR comment by its GraphQL node ID."""
        query = (
            "mutation($id: ID!, $body: String!) {"
            "  updateIssueComment(input: {id: $id, body: $body}) {"
            "    issueComment { id }"
            "  }"
            "}"
        )
        run_command(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"id={node_id}",
                "-f",
                f"body={body}",
            ]
        )

    def submit_pr_review(
        self,
        pr: PRCandidate,
        body_file: str,
        decision: Literal["approve", "request_changes"],
    ) -> None:
        decision_flag = "--approve" if decision == "approve" else "--request-changes"
        run_command(["gh", "pr", "review", pr.url, decision_flag, "--body-file", body_file])
