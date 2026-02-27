from __future__ import annotations

from dataclasses import dataclass

from pr_reviewer.config import AppConfig
from pr_reviewer.models import PRCandidate
from pr_reviewer.shell import run_command, run_json


@dataclass(slots=True)
class GitHubClient:
    viewer_login: str

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
