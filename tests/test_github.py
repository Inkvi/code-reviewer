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
