from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from pr_reviewer.config import AppConfig
from pr_reviewer.models import PRCandidate
from pr_reviewer.shell import run_command, run_json


@dataclass(slots=True)
class GitHubClient:
    viewer_login: str

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

    def discover_pr_candidates(self, config: AppConfig) -> list[PRCandidate]:
        data = run_json(
            [
                "gh",
                "search",
                "prs",
                "--owner",
                config.github_org,
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

        if not isinstance(data, list):
            return []

        candidates: list[PRCandidate] = []
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
                    "baseRefName,headRefOid",
                ]
            )

            candidates.append(
                PRCandidate(
                    owner=owner,
                    repo=repo,
                    number=int(item["number"]),
                    url=item["url"],
                    title=item.get("title", ""),
                    author_login=author_login,
                    base_ref=details.get("baseRefName", "main"),
                    head_sha=details.get("headRefOid", ""),
                    updated_at=item.get("updatedAt", ""),
                )
            )

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
                "number,url,title,author,baseRefName,headRefOid,updatedAt",
            ]
        )
        author = details.get("author") or {}
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
        )

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

    def post_pr_comment(self, pr: PRCandidate, body_file: str) -> None:
        run_command(["gh", "pr", "comment", pr.url, "--body-file", body_file])

    def submit_pr_review(
        self,
        pr: PRCandidate,
        body_file: str,
        decision: Literal["approve", "request_changes"],
    ) -> None:
        decision_flag = "--approve" if decision == "approve" else "--request-changes"
        run_command(["gh", "pr", "review", pr.url, decision_flag, "--body-file", body_file])
