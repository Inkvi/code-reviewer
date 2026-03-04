import subprocess

from pr_reviewer.config import AppConfig
from pr_reviewer.github import GitHubClient
from pr_reviewer.models import PRCandidate


def test_discover_pr_candidates_skips_excluded_repo_and_sets_latest_rerequest(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_orgs=["polymerdao"], excluded_repos=["polymerdao/infra"])

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
            return {
                "baseRefName": "main",
                "headRefOid": "deadbeef",
                "additions": 17,
                "deletions": 3,
                "files": [{"path": "src/app.py"}, {"path": ".github/workflows/ci.yaml"}],
            }
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
    assert candidates[0].additions == 17
    assert candidates[0].deletions == 3
    assert candidates[0].changed_file_paths == ["src/app.py", ".github/workflows/ci.yaml"]


def test_discover_pr_candidates_warns_and_continues_when_events_fail(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_orgs=["polymerdao"])

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
            return {
                "baseRefName": "main",
                "headRefOid": "deadbeef",
                "additions": 12,
                "deletions": 2,
                "files": [{"path": "src/main.py"}],
            }
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
    assert candidates[0].additions == 12
    assert candidates[0].deletions == 2
    assert candidates[0].changed_file_paths == ["src/main.py"]
    assert any("failed to fetch review-request events" in message for message in warnings)


def test_discover_pr_candidates_queries_all_configured_owners(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_orgs=["polymerdao", "Inkvi"])
    search_owners: list[str] = []

    def fake_run_json(args):  # noqa: ANN001
        if args[:3] == ["gh", "search", "prs"]:
            owner_scope = args[4]
            search_owners.append(owner_scope)
            if owner_scope == "polymerdao":
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
            if owner_scope == "Inkvi":
                return [
                    {
                        "number": 11,
                        "repository": {"nameWithOwner": "Inkvi/personal-repo"},
                        "url": "https://github.com/Inkvi/personal-repo/pull/11",
                        "title": "personal pr",
                        "author": {"login": "bob"},
                        "isDraft": False,
                        "updatedAt": "2026-02-27T20:10:00Z",
                    }
                ]
            raise AssertionError(f"unexpected owner scope: {owner_scope}")

        if args[:3] == ["gh", "pr", "view"]:
            return {
                "baseRefName": "main",
                "headRefOid": "deadbeef",
                "additions": 5,
                "deletions": 1,
                "files": [{"path": "src/app.py"}],
            }
        raise AssertionError(f"unexpected args: {args}")

    def fake_run_command(_args, **_kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Inkvi\t2026-02-27T20:05:00Z\n",
            stderr="",
        )

    monkeypatch.setattr("pr_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("pr_reviewer.github.run_command", fake_run_command)

    candidates = client.discover_pr_candidates(config)

    assert search_owners == ["polymerdao", "Inkvi"]
    assert [candidate.key for candidate in candidates] == [
        "polymerdao/obul#64",
        "Inkvi/personal-repo#11",
    ]


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
            "additions": 9,
            "deletions": 1,
            "files": [{"path": "README.md"}, {"path": "config/settings.toml"}],
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
    assert pr.additions == 9
    assert pr.deletions == 1
    assert pr.changed_file_paths == ["README.md", "config/settings.toml"]


def test_get_pr_issue_comments_formats_and_limits(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    pr = PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
    )

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        expected_prefix = [
            "gh",
            "api",
            "--paginate",
            "repos/polymerdao/obul/issues/64/comments",
        ]
        assert args[:4] == expected_prefix
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                '["alice","2026-02-27T20:00:00Z","  first\\ncomment  "]\n'
                '["bob","2026-02-27T20:01:00Z","second comment"]\n'
                '["carol","2026-02-27T20:02:00Z","third comment"]\n'
            ),
            stderr="",
        )

    monkeypatch.setattr("pr_reviewer.github.run_command", fake_run_command)

    comments = client.get_pr_issue_comments(pr, max_comments=2, per_comment_chars=50)

    assert comments == [
        "@bob (2026-02-27T20:01:00Z): second comment",
        "@carol (2026-02-27T20:02:00Z): third comment",
    ]
