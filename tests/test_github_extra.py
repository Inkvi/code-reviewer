"""Additional tests for github.py covering untested methods."""
import subprocess

from code_reviewer.github import GitHubClient
from code_reviewer.models import PRCandidate


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="polymerdao", repo="obul", number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test", author_login="alice", base_ref="main",
        head_sha="deadbeef", updated_at="2026-02-27T20:00:00Z",
    )


# --- _normalize_iso_timestamp ---


def test_normalize_iso_timestamp_valid() -> None:
    result = GitHubClient._normalize_iso_timestamp("2026-03-02T00:00:00Z")
    assert result is not None
    assert "+00:00" in result


def test_normalize_iso_timestamp_with_offset() -> None:
    result = GitHubClient._normalize_iso_timestamp("2026-03-02T00:00:00+05:00")
    assert result is not None


def test_normalize_iso_timestamp_none() -> None:
    assert GitHubClient._normalize_iso_timestamp(None) is None


def test_normalize_iso_timestamp_empty() -> None:
    assert GitHubClient._normalize_iso_timestamp("") is None
    assert GitHubClient._normalize_iso_timestamp("   ") is None


def test_normalize_iso_timestamp_invalid() -> None:
    assert GitHubClient._normalize_iso_timestamp("not-a-date") is None


def test_normalize_iso_timestamp_non_string() -> None:
    assert GitHubClient._normalize_iso_timestamp(12345) is None  # type: ignore[arg-type]


# --- _collapse_whitespace ---


def test_collapse_whitespace() -> None:
    assert GitHubClient._collapse_whitespace("hello   world") == "hello world"
    assert GitHubClient._collapse_whitespace("  a\n\t b  c  ") == "a b c"
    assert GitHubClient._collapse_whitespace("single") == "single"


# --- _extract_changed_file_paths ---


def test_extract_changed_file_paths_valid() -> None:
    details = {"files": [{"path": "src/app.py"}, {"path": "README.md"}]}
    paths = GitHubClient._extract_changed_file_paths(details)
    assert paths == ["src/app.py", "README.md"]


def test_extract_changed_file_paths_empty() -> None:
    assert GitHubClient._extract_changed_file_paths({}) == []
    assert GitHubClient._extract_changed_file_paths({"files": []}) == []


def test_extract_changed_file_paths_not_dict() -> None:
    assert GitHubClient._extract_changed_file_paths("not a dict") == []


def test_extract_changed_file_paths_skips_invalid_entries() -> None:
    details = {"files": [{"path": "valid.py"}, "not-a-dict", {"path": ""}, {"path": "  "}]}
    paths = GitHubClient._extract_changed_file_paths(details)
    assert paths == ["valid.py"]


# --- _is_repo_excluded ---


def test_is_repo_excluded_full_name() -> None:
    from code_reviewer.config import AppConfig
    config = AppConfig(github_orgs=["polymerdao"], excluded_repos=["polymerdao/infra"])
    assert GitHubClient._is_repo_excluded(config, "polymerdao", "infra") is True
    assert GitHubClient._is_repo_excluded(config, "polymerdao", "obul") is False


def test_is_repo_excluded_bare_name() -> None:
    from code_reviewer.config import AppConfig
    config = AppConfig(github_orgs=["polymerdao"], excluded_repos=["infra"])
    assert GitHubClient._is_repo_excluded(config, "polymerdao", "infra") is True
    assert GitHubClient._is_repo_excluded(config, "other-org", "infra") is True


def test_is_repo_excluded_empty() -> None:
    from code_reviewer.config import AppConfig
    config = AppConfig(github_orgs=["polymerdao"])
    assert GitHubClient._is_repo_excluded(config, "polymerdao", "infra") is False


# --- get_pr_head_sha ---


def test_get_pr_head_sha(monkeypatch) -> None:
    pr = _sample_pr()

    def fake_run_json(args, **_kwargs):  # noqa: ANN001
        return {"headRefOid": "newsha123"}

    monkeypatch.setattr("code_reviewer.github.run_json", fake_run_json)
    sha = GitHubClient.get_pr_head_sha(pr)
    assert sha == "newsha123"


# --- has_issue_comment_by_viewer ---


def test_has_issue_comment_by_viewer_true(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    pr = _sample_pr()

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="alice\nInkvi\nbob\n", stderr=""
        )

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)
    assert client.has_issue_comment_by_viewer(pr) is True


def test_has_issue_comment_by_viewer_false(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    pr = _sample_pr()

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="alice\nbob\n", stderr=""
        )

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)
    assert client.has_issue_comment_by_viewer(pr) is False


# --- post_pr_comment ---


def test_post_pr_comment_calls_gh(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    pr = _sample_pr()
    captured: list[list[str]] = []

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        captured.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)
    client.post_pr_comment(pr, "/tmp/review.md")

    assert len(captured) == 1
    assert "pr" in captured[0]
    assert "comment" in captured[0]
    assert "--body-file" in captured[0]


# --- post_pr_comment_inline ---


def test_post_pr_comment_inline_calls_gh(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    pr = _sample_pr()
    captured: list[list[str]] = []

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        captured.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)
    client.post_pr_comment_inline(pr, "Starting review…")

    assert len(captured) == 1
    assert "--body" in captured[0]


# --- submit_pr_review ---


def test_submit_pr_review_approve(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    pr = _sample_pr()
    captured: list[list[str]] = []

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        captured.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)
    client.submit_pr_review(pr, "/tmp/review.md", "approve")

    assert "--approve" in captured[0]


def test_submit_pr_review_request_changes(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    pr = _sample_pr()
    captured: list[list[str]] = []

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        captured.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)
    client.submit_pr_review(pr, "/tmp/review.md", "request_changes")

    assert "--request-changes" in captured[0]


# --- _is_slash_command_authorized ---


def test_slash_command_authorized_pr_author() -> None:
    client = GitHubClient(viewer_login="Inkvi")
    # PR author should always be authorized
    assert client._is_slash_command_authorized("polymerdao", "alice", "alice") is True


def test_slash_command_authorized_org_member(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)
    assert client._is_slash_command_authorized("polymerdao", "bob", "alice") is True


def test_slash_command_unauthorized(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        raise RuntimeError("not a member")

    monkeypatch.setattr("code_reviewer.github.run_command", fake_run_command)
    assert client._is_slash_command_authorized("polymerdao", "outsider", "alice") is False


# --- _parse_owner_repo_from_pr_url edge cases ---


def test_parse_owner_repo_invalid_path() -> None:
    import pytest
    with pytest.raises(ValueError, match="Invalid GitHub PR URL"):
        GitHubClient._parse_owner_repo_from_pr_url("https://github.com/just-owner")


def test_parse_owner_repo_empty_host() -> None:
    import pytest
    with pytest.raises(ValueError):
        GitHubClient._parse_owner_repo_from_pr_url("not-a-url")
