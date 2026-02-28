from pr_reviewer.config import AppConfig
from pr_reviewer.github import GitHubClient


def test_discover_pr_candidates_skips_excluded_repo(monkeypatch):
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_org="polymerdao", excluded_repos=["polymerdao/infra"])

    def fake_run_json(args):
        if args[:3] == ["gh", "search", "prs"]:
            return [
                {
                    "number": 64,
                    "repository": {"nameWithOwner": "polymerdao/obul"},
                    "url": "https://github.com/polymerdao/obul/pull/64",
                    "title": "obul pr",
                    "author": {"login": "alice"},
                    "isDraft": False,
                    "updatedAt": "2026-02-27T20:00:00Z",
                },
                {
                    "number": 3204,
                    "repository": {"nameWithOwner": "polymerdao/infra"},
                    "url": "https://github.com/polymerdao/infra/pull/3204",
                    "title": "infra pr",
                    "author": {"login": "bob"},
                    "isDraft": False,
                    "updatedAt": "2026-02-27T20:01:00Z",
                },
            ]
        if args[:3] == ["gh", "pr", "view"]:
            return {"baseRefName": "main", "headRefOid": "deadbeef"}
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr("pr_reviewer.github.run_json", fake_run_json)

    candidates = client.discover_pr_candidates(config)

    assert len(candidates) == 1
    assert candidates[0].owner == "polymerdao"
    assert candidates[0].repo == "obul"
    assert candidates[0].number == 64


def test_parse_owner_repo_from_pr_url() -> None:
    owner, repo = GitHubClient._parse_owner_repo_from_pr_url(
        "https://github.com/polymerdao/obul/pull/64"
    )
    assert owner == "polymerdao"
    assert repo == "obul"


def test_parse_owner_repo_from_pr_url_invalid_host() -> None:
    try:
        GitHubClient._parse_owner_repo_from_pr_url("https://gitlab.com/polymerdao/obul/pull/64")
    except ValueError as exc:
        assert "Unsupported PR URL host" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unsupported host")


def test_get_pr_candidate(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")

    def fake_run_json(args):
        assert args[:4] == ["gh", "pr", "view", "https://github.com/polymerdao/obul/pull/64"]
        return {
            "number": 64,
            "url": "https://github.com/polymerdao/obul/pull/64",
            "title": "bump versions",
            "author": {"login": "alice"},
            "baseRefName": "main",
            "headRefOid": "deadbeef",
            "updatedAt": "2026-02-27T20:00:00Z",
        }

    monkeypatch.setattr("pr_reviewer.github.run_json", fake_run_json)

    pr = client.get_pr_candidate("https://github.com/polymerdao/obul/pull/64")

    assert pr.owner == "polymerdao"
    assert pr.repo == "obul"
    assert pr.number == 64
    assert pr.base_ref == "main"
    assert pr.head_sha == "deadbeef"
