import subprocess
from pathlib import Path

from code_reviewer.config import AppConfig
from code_reviewer.github import GitHubClient
from code_reviewer.models import PRCandidate
from code_reviewer.state import StateStore


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

    monkeypatch.setattr("code_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

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

    monkeypatch.setattr("code_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)
    monkeypatch.setattr("code_reviewer.github.warn", warnings.append)

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

    monkeypatch.setattr("code_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

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
            stdout=("Alice\t2026-02-27T20:00:00Z\nInkvi\t2026-02-27T20:05:00Z\n"),
            stderr="",
        )

    monkeypatch.setattr("code_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

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

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    comments = client.get_pr_issue_comments(pr, max_comments=2, per_comment_chars=50)

    assert comments == [
        "@bob (2026-02-27T20:01:00Z): second comment",
        "@carol (2026-02-27T20:02:00Z): third comment",
    ]


def test_get_pr_issue_comments_skips_bot_review_in_progress(monkeypatch) -> None:
    client = GitHubClient(viewer_login="bot")
    pr = PRCandidate(
        owner="org",
        repo="repo",
        number=1,
        url="https://github.com/org/repo/pull/1",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="abc",
        updated_at="2026-01-01T00:00:00Z",
    )

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                '["bot","2026-01-01T01:00:00Z",'
                '"**Review in progress** (full review)\\n| Stage |"]\n'
                '["alice","2026-01-01T02:00:00Z","Fixed the bug"]\n'
                '["bot","2026-01-01T03:00:00Z","Normal bot comment"]\n'
            ),
            stderr="",
        )

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    comments = client.get_pr_issue_comments(pr)

    # Bot "Review in progress" skipped; alice's comment and bot's
    # non-status comment are kept
    assert len(comments) == 2
    assert "Fixed the bug" in comments[0]
    assert "Normal bot comment" in comments[1]


def test_get_pr_review_findings_returns_bot_reviews(monkeypatch) -> None:
    client = GitHubClient(viewer_login="monitoring-dev")
    pr = PRCandidate(
        owner="polymerdao",
        repo="signer-service",
        number=18,
        url="https://github.com/polymerdao/signer-service/pull/18",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-03-26T00:00:00Z",
    )

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        assert "pulls/18/reviews" in args[3]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                '["monitoring-dev","2026-03-26T01:00:00Z",'
                '"### Findings\\n- [P1] bug","CHANGES_REQUESTED"]\n'
                '["alice","2026-03-26T01:30:00Z",'
                '"LGTM","APPROVED"]\n'
                '["monitoring-dev","2026-03-26T02:00:00Z",'
                '"### Findings\\n- [P2] nit","CHANGES_REQUESTED"]\n'
                '["monitoring-dev","2026-03-26T03:00:00Z",'
                '"No issues found","APPROVED"]\n'
            ),
            stderr="",
        )

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    findings = client.get_pr_review_findings(pr)

    # Only bot reviews with ### Findings; alice's and approval
    # without findings are excluded
    assert len(findings) == 2
    assert "[P1] bug" in findings[0]
    assert "[P2] nit" in findings[1]


def test_get_pr_review_findings_empty_when_no_reviews(monkeypatch) -> None:
    client = GitHubClient(viewer_login="bot")
    pr = PRCandidate(
        owner="org",
        repo="repo",
        number=1,
        url="https://github.com/org/repo/pull/1",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="abc",
        updated_at="2026-01-01T00:00:00Z",
    )

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    assert client.get_pr_review_findings(pr) == []


def test_add_eyes_reaction_calls_gh_api(monkeypatch) -> None:
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

    captured_args: list[list[str]] = []

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        captured_args.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    client.add_eyes_reaction(pr)

    assert len(captured_args) == 1
    assert captured_args[0] == [
        "gh",
        "api",
        "repos/polymerdao/obul/issues/64/reactions",
        "-f",
        "content=eyes",
        "--silent",
    ]


def test_check_org_membership_returns_true_for_member(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")

    def fake_run_command(args, **_kwargs):
        assert "orgs/polymerdao/members/alice" in args[2]
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    assert client.check_org_membership("polymerdao", "alice") is True


def test_check_org_membership_returns_false_on_error(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")

    def fake_run_command(args, **_kwargs):
        raise RuntimeError("not a member")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    assert client.check_org_membership("polymerdao", "alice") is False


def test_add_reaction_to_comment_calls_gh_api(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")

    captured_args: list[list[str]] = []

    def fake_run_command(args, **_kwargs):
        captured_args.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    client.add_reaction_to_comment("polymerdao", "obul", 123456, "eyes")

    assert len(captured_args) == 1
    assert "repos/polymerdao/obul/issues/comments/123456/reactions" in captured_args[0]
    assert "content=eyes" in captured_args[0]


def test_discover_slash_command_candidates_finds_review_comment(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=True)

    def fake_run_json(args):
        if args[:3] == ["gh", "search", "prs"]:
            return [
                {
                    "number": 64,
                    "repository": {"nameWithOwner": "polymerdao/obul"},
                    "url": "https://github.com/polymerdao/obul/issues/64",
                    "title": "test pr",
                    "author": {"login": "alice"},
                    "updatedAt": "2026-03-05T10:00:00Z",
                }
            ]
        if args[:3] == ["gh", "pr", "view"]:
            return {
                "number": 64,
                "url": "https://github.com/polymerdao/obul/pull/64",
                "title": "test pr",
                "author": {"login": "alice"},
                "baseRefName": "main",
                "headRefOid": "deadbeef",
                "updatedAt": "2026-03-05T10:00:00Z",
                "additions": 20,
                "deletions": 5,
                "files": [{"path": "src/app.py"}],
            }
        raise AssertionError(f"unexpected args: {args}")

    def fake_run_command(args, **_kwargs):
        cmd_str = " ".join(str(a) for a in args)
        if "issues" in cmd_str and "comments" in cmd_str and "--jq" in cmd_str:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout='{"id":123456,"user":"alice","created_at":"2026-03-05T10:05:00Z","body":"/review"}\n',
                stderr="",
            )
        if "members" in cmd_str:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    store = StateStore(Path("/tmp/fake-state.json"))
    store._data = {}

    candidates = client.discover_slash_command_candidates(config, store)

    assert len(candidates) == 1
    assert candidates[0].key == "polymerdao/obul#64"
    assert candidates[0].slash_command_trigger is not None
    assert candidates[0].slash_command_trigger.comment_id == 123456
    assert candidates[0].slash_command_trigger.force is False


def test_discover_slash_command_candidates_detects_force(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=True)

    def fake_run_json(args):
        if args[:3] == ["gh", "search", "prs"]:
            return [
                {
                    "number": 64,
                    "repository": {"nameWithOwner": "polymerdao/obul"},
                    "url": "https://github.com/polymerdao/obul/issues/64",
                    "title": "test pr",
                    "author": {"login": "alice"},
                    "updatedAt": "2026-03-05T10:00:00Z",
                }
            ]
        if args[:3] == ["gh", "pr", "view"]:
            return {
                "number": 64,
                "url": "https://github.com/polymerdao/obul/pull/64",
                "title": "test pr",
                "author": {"login": "alice"},
                "baseRefName": "main",
                "headRefOid": "deadbeef",
                "updatedAt": "2026-03-05T10:00:00Z",
                "additions": 20,
                "deletions": 5,
                "files": [{"path": "src/app.py"}],
            }
        raise AssertionError(f"unexpected args: {args}")

    def fake_run_command(args, **_kwargs):
        cmd_str = " ".join(str(a) for a in args)
        if "comments" in cmd_str and "--jq" in cmd_str:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    '{"id":123456,"user":"alice","created_at":'
                    '"2026-03-05T10:05:00Z","body":"/review force"}\n'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    store = StateStore(Path("/tmp/fake-state.json"))
    store._data = {}

    candidates = client.discover_slash_command_candidates(config, store)

    assert len(candidates) == 1
    assert candidates[0].slash_command_trigger is not None
    assert candidates[0].slash_command_trigger.force is True


def test_discover_slash_command_candidates_skips_already_processed(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=True)

    def fake_run_json(args):
        if args[:3] == ["gh", "search", "prs"]:
            return [
                {
                    "number": 64,
                    "repository": {"nameWithOwner": "polymerdao/obul"},
                    "url": "https://github.com/polymerdao/obul/issues/64",
                    "title": "test pr",
                    "author": {"login": "alice"},
                    "updatedAt": "2026-03-05T10:00:00Z",
                }
            ]
        raise AssertionError(f"unexpected args: {args}")

    def fake_run_command(args, **_kwargs):
        cmd_str = " ".join(str(a) for a in args)
        if "comments" in cmd_str and "--jq" in cmd_str:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout='{"id":123456,"user":"alice","created_at":"2026-03-05T10:05:00Z","body":"/review"}\n',
                stderr="",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    store = StateStore(Path("/tmp/fake-state.json"))
    store._data = {"polymerdao/obul#64": {"last_slash_command_id": 123456}}

    candidates = client.discover_slash_command_candidates(config, store)

    assert len(candidates) == 0


def test_discover_slash_command_candidates_disabled(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=False)

    store = StateStore(Path("/tmp/fake-state.json"))
    store._data = {}

    candidates = client.discover_slash_command_candidates(config, store)
    assert len(candidates) == 0


def test_create_pr_comment_returns_node_id(monkeypatch) -> None:
    client = GitHubClient(viewer_login="bot")
    pr = PRCandidate(
        owner="org",
        repo="repo",
        number=1,
        url="https://github.com/org/repo/pull/1",
        title="t",
        author_login="a",
        base_ref="main",
        head_sha="abc123",
        updated_at="",
    )

    captured: list[list[str]] = []

    def fake_run_json(args, **_kwargs):
        captured.append(args)
        return {"node_id": "IC_abc123"}

    monkeypatch.setattr("code_reviewer.github.run_json", fake_run_json)
    node_id = client.create_pr_comment(pr, "hello")

    assert node_id == "IC_abc123"
    assert captured[0][:3] == ["gh", "api", "repos/org/repo/issues/1/comments"]


def test_edit_pr_comment_calls_graphql(monkeypatch) -> None:
    client = GitHubClient(viewer_login="bot")

    captured: list[list[str]] = []

    def fake_run_command(args, **_kwargs):
        captured.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)
    client.edit_pr_comment("IC_abc123", "updated body")

    assert captured[0][:3] == ["gh", "api", "graphql"]
    # The query arg is passed via -f query=...
    query_args = " ".join(captured[0])
    assert "updateIssueComment" in query_args


def test_discover_slash_command_candidates_rejects_unauthorized_user(monkeypatch) -> None:
    """A non-org-member who is not the PR author should be ignored."""
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=True)

    def fake_run_json(args):  # noqa: ANN001
        if args[:3] == ["gh", "search", "prs"]:
            return [
                {
                    "number": 64,
                    "repository": {"nameWithOwner": "polymerdao/obul"},
                    "url": "https://github.com/polymerdao/obul/issues/64",
                    "title": "test pr",
                    "author": {"login": "alice"},
                    "updatedAt": "2026-03-05T10:00:00Z",
                }
            ]
        raise AssertionError(f"unexpected args: {args}")

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        cmd_str = " ".join(str(a) for a in args)
        if "comments" in cmd_str and "--jq" in cmd_str:
            # Comment from "outsider" who is neither PR author nor org member.
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    '{"id":999999,"user":"outsider",'
                    '"created_at":"2026-03-05T10:05:00Z","body":"/review"}\n'
                ),
                stderr="",
            )
        if "members" in cmd_str:
            # Not an org member.
            raise RuntimeError("not a member")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)

    store = StateStore(Path("/tmp/fake-state.json"))
    store._data = {}

    candidates = client.discover_slash_command_candidates(config, store)

    assert len(candidates) == 0
