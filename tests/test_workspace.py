from pathlib import Path

from code_reviewer.models import PRCandidate
from code_reviewer.workspace import PRWorkspace


def _sample_pr() -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="bridge-master",
        number=18,
        url="https://github.com/polymerdao/bridge-master/pull/18",
        title="Update bridge logic",
        author_login="alice",
        base_ref="main",
        head_sha="08e7522e1234567890abcdef",
        updated_at="2026-03-20T05:34:01Z",
    )


def test_update_to_latest_detaches_before_force_fetch(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run_command(args, **_kwargs):  # noqa: ANN001
        calls.append(args)
        return None

    monkeypatch.setattr("code_reviewer.workspace.run_command", fake_run_command)

    workdir = Path("/tmp/workspace")
    PRWorkspace.update_to_latest(workdir, _sample_pr())

    assert calls == [
        ["git", "-C", str(workdir), "checkout", "--quiet", "--detach"],
        [
            "git",
            "-C",
            str(workdir),
            "fetch",
            "--quiet",
            "origin",
            "pull/18/head:pr-18",
            "--force",
        ],
        ["git", "-C", str(workdir), "checkout", "--quiet", "pr-18"],
    ]
