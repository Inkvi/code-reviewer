import subprocess

from pr_reviewer.config import AppConfig
from pr_reviewer.github import GitHubClient


def test_discover_pr_candidates_skips_excluded_repo_and_sets_latest_rerequest(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_org="polymerdao", excluded_repos=["polymerdao/infra"])

    def fake_run_json(args):  # noqa: ANN001
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

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        expected_prefix = [
            "gh",
            "api",
            "--paginate",
            "repos/polymerdao/obul/issues/64/events",
        ]
        assert args[:4] == expected_prefix
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                "Alice\t2026-02-27T20:00:00Z\n"
                "Inkvi\t2026-02-27T20:05:00Z\n"
                "Inkvi\t2026-02-27T20:10:00Z\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("pr_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("pr_reviewer.github.run_command", fake_run_command)

    candidates = client.discover_pr_candidates(config)

    assert len(candidates) == 1
    assert candidates[0].owner == "polymerdao"
    assert candidates[0].repo == "obul"
    assert candidates[0].number == 64
    assert candidates[0].latest_direct_rerequest_at == "2026-02-27T20:10:00+00:00"
    assert candidates[0].trigger_metadata_version == 1


def test_discover_pr_candidates_warns_and_continues_when_events_fail(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_org="polymerdao")

    def fake_run_json(args):  # noqa: ANN001
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
                }
            ]
        if args[:3] == ["gh", "pr", "view"]:
            return {"baseRefName": "main", "headRefOid": "deadbeef"}
        raise AssertionError(f"unexpected args: {args}")

    warnings: list[str] = []

    def fake_run_command(*_args, **_kwargs):  # noqa: ANN001
        raise RuntimeError("events API unavailable")

    monkeypatch.setattr("pr_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("pr_reviewer.github.run_command", fake_run_command)
    monkeypatch.setattr("pr_reviewer.github.warn", warnings.append)

    candidates = client.discover_pr_candidates(config)

    assert len(candidates) == 1
    assert candidates[0].latest_direct_rerequest_at is None
    assert any("failed to fetch review-request events" in message for message in warnings)


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


def test_get_pr_candidate_sets_latest_direct_rerequest(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")

    def fake_run_json(args):  # noqa: ANN001
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

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        expected_prefix = [
            "gh",
            "api",
            "--paginate",
            "repos/polymerdao/obul/issues/64/events",
        ]
        assert args[:4] == expected_prefix
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                "Alice\t2026-02-27T20:00:00Z\n"
                "Inkvi\t2026-02-27T20:05:00Z\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("pr_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("pr_reviewer.github.run_command", fake_run_command)

    pr = client.get_pr_candidate("https://github.com/polymerdao/obul/pull/64")

    assert pr.owner == "polymerdao"
    assert pr.repo == "obul"
    assert pr.number == 64
    assert pr.base_ref == "main"
    assert pr.head_sha == "deadbeef"
    assert pr.latest_direct_rerequest_at == "2026-02-27T20:05:00+00:00"
