# Slash Command Trigger Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `/review` and `/review force` slash command triggers so anyone (PR author or org member) can request a review by commenting on a PR, without needing to assign the bot as a reviewer.

**Architecture:** A new `SlashCommandTrigger` dataclass carries metadata about the triggering comment through the pipeline. A new `discover_slash_command_candidates` method on `GitHubClient` scans recent PR comments across monitored orgs. The processor gains a parallel code path that bypasses re-request logic and instead checks HEAD SHA staleness. All wired together in the daemon's poll cycle with deduplication.

**Tech Stack:** Python, gh CLI, existing test patterns (monkeypatch + DummyStore/DummyWorkspace)

---

### Task 1: Add config field `slash_command_enabled`

**Files:**
- Modify: `src/pr_reviewer/config.py:9-39` (AppConfig class)
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

In `tests/test_config.py`, add:

```python
def test_load_config_slash_command_enabled_defaults_true(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["Inkvi"]\n', encoding="utf-8")

    cfg = load_config(path)

    assert cfg.slash_command_enabled is True


def test_load_config_slash_command_enabled_set_false(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('github_orgs=["Inkvi"]\nslash_command_enabled = false\n', encoding="utf-8")

    cfg = load_config(path)

    assert cfg.slash_command_enabled is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_load_config_slash_command_enabled_defaults_true -v`
Expected: FAIL with AttributeError

**Step 3: Write minimal implementation**

In `src/pr_reviewer/config.py`, add to `AppConfig` class (after `post_rerequest_comment` field):

```python
slash_command_enabled: bool = True
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/config.py tests/test_config.py
git commit -m "feat: add slash_command_enabled config field"
```

---

### Task 2: Add `SlashCommandTrigger` model and extend `PRCandidate`

**Files:**
- Modify: `src/pr_reviewer/models.py`
- Test: `tests/test_processor.py` (update `_sample_pr` helper)

**Step 1: Write the failing test**

In a new file `tests/test_slash_command.py`:

```python
from pr_reviewer.models import PRCandidate, SlashCommandTrigger


def test_slash_command_trigger_defaults() -> None:
    trigger = SlashCommandTrigger(
        comment_id=123456,
        comment_author="alice",
        comment_created_at="2026-03-05T10:00:00+00:00",
        force=False,
    )
    assert trigger.comment_id == 123456
    assert trigger.force is False


def test_pr_candidate_slash_command_trigger_default_none() -> None:
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
    assert pr.slash_command_trigger is None


def test_pr_candidate_with_slash_command_trigger() -> None:
    trigger = SlashCommandTrigger(
        comment_id=123456,
        comment_author="alice",
        comment_created_at="2026-03-05T10:00:00+00:00",
        force=True,
    )
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
        slash_command_trigger=trigger,
    )
    assert pr.slash_command_trigger is not None
    assert pr.slash_command_trigger.force is True
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_slash_command.py -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

In `src/pr_reviewer/models.py`, add before `PRCandidate`:

```python
@dataclass(slots=True)
class SlashCommandTrigger:
    comment_id: int
    comment_author: str
    comment_created_at: str
    force: bool = False
```

In `PRCandidate`, add field (after `changed_file_paths`):

```python
slash_command_trigger: SlashCommandTrigger | None = None
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_slash_command.py tests/test_processor.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/models.py tests/test_slash_command.py
git commit -m "feat: add SlashCommandTrigger model and field on PRCandidate"
```

---

### Task 3: Add `ProcessedState.last_reviewed_head_sha` tracking for slash command dedup

**Files:**
- Modify: `src/pr_reviewer/models.py` (ProcessedState — already has `last_reviewed_head_sha`)
- Modify: `src/pr_reviewer/models.py` (ProcessedState — add `last_slash_command_id`)
- Modify: `src/pr_reviewer/state.py`
- Test: `tests/test_state.py`

**Step 1: Write the failing test**

In `tests/test_state.py`, add:

```python
def test_state_store_persists_last_slash_command_id(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store._owns_lock = True
    store.load()

    store.set(
        "polymerdao/obul#64",
        ProcessedState(
            last_processed_at="2026-03-05T00:00:00+00:00",
            last_slash_command_id=123456,
        ),
    )
    store.save()

    store2 = StateStore(state_path)
    store2._owns_lock = True
    store2.load()
    state = store2.get("polymerdao/obul#64")
    assert state.last_slash_command_id == 123456
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_state.py::test_state_store_persists_last_slash_command_id -v`
Expected: FAIL

**Step 3: Write minimal implementation**

In `src/pr_reviewer/models.py`, add field to `ProcessedState`:

```python
last_slash_command_id: int | None = None
```

In `src/pr_reviewer/state.py`, update `get` method to include:

```python
last_slash_command_id=item.get("last_slash_command_id"),
```

(Parse as int if present):
```python
_raw_cmd_id = item.get("last_slash_command_id")
last_slash_command_id = int(_raw_cmd_id) if _raw_cmd_id is not None else None
```

Update `set` method to include:

```python
"last_slash_command_id": state.last_slash_command_id,
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_state.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/models.py src/pr_reviewer/state.py tests/test_state.py
git commit -m "feat: add last_slash_command_id to ProcessedState"
```

---

### Task 4: Add `GitHubClient.check_org_membership` method

**Files:**
- Modify: `src/pr_reviewer/github.py`
- Test: `tests/test_github.py`

**Step 1: Write the failing test**

In `tests/test_github.py`, add:

```python
def test_check_org_membership_returns_true_for_member(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")

    def fake_run_command(args, **_kwargs):
        assert "repos" not in args  # Should use orgs endpoint
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("pr_reviewer.github.run_command", fake_run_command)

    assert client.check_org_membership("polymerdao", "alice") is True


def test_check_org_membership_returns_false_on_error(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")

    def fake_run_command(args, **_kwargs):
        raise RuntimeError("not a member")

    monkeypatch.setattr("pr_reviewer.github.run_command", fake_run_command)

    assert client.check_org_membership("polymerdao", "alice") is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_github.py::test_check_org_membership_returns_true_for_member -v`
Expected: FAIL with AttributeError

**Step 3: Write minimal implementation**

In `src/pr_reviewer/github.py`, add to `GitHubClient`:

```python
@staticmethod
def check_org_membership(org: str, login: str) -> bool:
    try:
        run_command(
            ["gh", "api", f"orgs/{org}/members/{login}", "--silent"]
        )
        return True
    except Exception:  # noqa: BLE001
        return False
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_github.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/github.py tests/test_github.py
git commit -m "feat: add check_org_membership to GitHubClient"
```

---

### Task 5: Add `GitHubClient.add_reaction_to_comment` method

**Files:**
- Modify: `src/pr_reviewer/github.py`
- Test: `tests/test_github.py`

**Step 1: Write the failing test**

```python
def test_add_reaction_to_comment_calls_gh_api(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")

    captured_args: list[list[str]] = []

    def fake_run_command(args, **_kwargs):
        captured_args.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("pr_reviewer.github.run_command", fake_run_command)

    client.add_reaction_to_comment("polymerdao", "obul", 123456, "eyes")

    assert len(captured_args) == 1
    assert "repos/polymerdao/obul/issues/comments/123456/reactions" in captured_args[0]
    assert "content=eyes" in captured_args[0]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_github.py::test_add_reaction_to_comment_calls_gh_api -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
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
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_github.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/github.py tests/test_github.py
git commit -m "feat: add add_reaction_to_comment to GitHubClient"
```

---

### Task 6: Add `GitHubClient.discover_slash_command_candidates` method

**Files:**
- Modify: `src/pr_reviewer/github.py`
- Test: `tests/test_github.py`

**Step 1: Write the failing test**

```python
def test_discover_slash_command_candidates_finds_review_comment(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=True)

    def fake_run_json(args):
        if args[:3] == ["gh", "search", "issues"]:
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
        endpoint = args[3] if len(args) > 3 else ""
        if "comments" in endpoint:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    '{"id":123456,"user":{"login":"alice"},"created_at":"2026-03-05T10:05:00Z","body":"/review"}\n'
                ),
                stderr="",
            )
        if "members" in endpoint:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("pr_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("pr_reviewer.github.run_command", fake_run_command)

    from pr_reviewer.state import StateStore
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
        if args[:3] == ["gh", "search", "issues"]:
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
        endpoint = args[3] if len(args) > 3 else ""
        if "comments" in endpoint:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    '{"id":123456,"user":{"login":"alice"},"created_at":"2026-03-05T10:05:00Z","body":"/review force"}\n'
                ),
                stderr="",
            )
        if "members" in endpoint:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("pr_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("pr_reviewer.github.run_command", fake_run_command)

    from pr_reviewer.state import StateStore
    store = StateStore(Path("/tmp/fake-state.json"))
    store._data = {}

    candidates = client.discover_slash_command_candidates(config, store)

    assert len(candidates) == 1
    assert candidates[0].slash_command_trigger.force is True


def test_discover_slash_command_candidates_skips_already_processed(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=True)

    def fake_run_json(args):
        if args[:3] == ["gh", "search", "issues"]:
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
        endpoint = args[3] if len(args) > 3 else ""
        if "comments" in endpoint:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    '{"id":123456,"user":{"login":"alice"},"created_at":"2026-03-05T10:05:00Z","body":"/review"}\n'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("pr_reviewer.github.run_json", fake_run_json)
    monkeypatch.setattr("pr_reviewer.github.run_command", fake_run_command)

    from pr_reviewer.state import StateStore
    store = StateStore(Path("/tmp/fake-state.json"))
    store._data = {
        "polymerdao/obul#64": {"last_slash_command_id": 123456},
    }

    candidates = client.discover_slash_command_candidates(config, store)

    assert len(candidates) == 0


def test_discover_slash_command_candidates_disabled(monkeypatch) -> None:
    client = GitHubClient(viewer_login="Inkvi")
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=False)

    from pr_reviewer.state import StateStore
    store = StateStore(Path("/tmp/fake-state.json"))
    store._data = {}

    candidates = client.discover_slash_command_candidates(config, store)

    assert len(candidates) == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_github.py::test_discover_slash_command_candidates_finds_review_comment -v`
Expected: FAIL with AttributeError

**Step 3: Write minimal implementation**

In `src/pr_reviewer/github.py`, add imports at top:

```python
import re
from pr_reviewer.models import SlashCommandTrigger
```

Add method to `GitHubClient`:

```python
_REVIEW_COMMAND_RE = re.compile(r"^\s*/review(?:\s+(force))?\s*$", re.MULTILINE)

def discover_slash_command_candidates(
    self,
    config: AppConfig,
    store: "StateStore",
) -> list[PRCandidate]:
    if not config.slash_command_enabled:
        return []

    candidates: list[PRCandidate] = []

    for owner_scope in config.github_owners:
        try:
            issues = run_json(
                [
                    "gh",
                    "search",
                    "issues",
                    "--owner",
                    owner_scope,
                    "--state",
                    "open",
                    "--type",
                    "pr",
                    "--json",
                    "number,repository,url,title,author,updatedAt",
                    "-L",
                    "200",
                    "--sort",
                    "updated",
                ]
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"Failed to search issues for slash commands in {owner_scope}: {exc}")
            continue

        if not isinstance(issues, list):
            continue

        for item in issues:
            repo_full = item.get("repository", {}).get("nameWithOwner", "")
            if "/" not in repo_full:
                continue
            owner, repo = repo_full.split("/", maxsplit=1)

            if self._is_repo_excluded(config, owner, repo):
                continue

            number = int(item["number"])
            pr_key = f"{owner}/{repo}#{number}"

            previous = store.get(pr_key)
            last_cmd_id = previous.last_slash_command_id

            trigger = self._find_latest_review_command(
                owner, repo, number, item.get("author", {}).get("login", ""),
                last_cmd_id,
            )
            if trigger is None:
                continue

            try:
                details = run_json(
                    [
                        "gh",
                        "pr",
                        "view",
                        f"https://github.com/{owner}/{repo}/pull/{number}",
                        "--json",
                        "number,url,title,author,baseRefName,headRefOid,"
                        "updatedAt,additions,deletions,files",
                    ]
                )
            except Exception as exc:  # noqa: BLE001
                warn(f"Failed to fetch PR details for {pr_key}: {exc}")
                continue

            author = details.get("author") or {}
            candidate = PRCandidate(
                owner=owner,
                repo=repo,
                number=number,
                url=details.get("url", f"https://github.com/{owner}/{repo}/pull/{number}"),
                title=details.get("title", ""),
                author_login=author.get("login", ""),
                base_ref=details.get("baseRefName", "main"),
                head_sha=details.get("headRefOid", ""),
                updated_at=details.get("updatedAt", ""),
                additions=int(details.get("additions", 0) or 0),
                deletions=int(details.get("deletions", 0) or 0),
                changed_file_paths=self._extract_changed_file_paths(details),
                slash_command_trigger=trigger,
            )
            candidates.append(candidate)

    return candidates

def _find_latest_review_command(
    self,
    owner: str,
    repo: str,
    number: int,
    pr_author: str,
    last_processed_command_id: int | None,
) -> SlashCommandTrigger | None:
    endpoint = f"repos/{owner}/{repo}/issues/{number}/comments"
    proc = run_command(
        [
            "gh",
            "api",
            "--paginate",
            endpoint,
            "--jq",
            ".[] | {id, user: .user.login, created_at, body} | @json",
        ]
    )

    best: SlashCommandTrigger | None = None
    for line in proc.stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        comment_id = payload.get("id")
        if not isinstance(comment_id, int):
            continue

        if last_processed_command_id is not None and comment_id <= last_processed_command_id:
            continue

        body = payload.get("body", "")
        match = self._REVIEW_COMMAND_RE.search(body)
        if match is None:
            continue

        login = payload.get("user", "")
        if not self._is_slash_command_authorized(owner, login, pr_author):
            continue

        force = match.group(1) == "force" if match.group(1) else False
        created_at = self._normalize_iso_timestamp(payload.get("created_at", ""))

        best = SlashCommandTrigger(
            comment_id=comment_id,
            comment_author=login,
            comment_created_at=created_at or "",
            force=force,
        )

    return best

def _is_slash_command_authorized(self, org: str, login: str, pr_author: str) -> bool:
    if login.lower() == pr_author.lower():
        return True
    return self.check_org_membership(org, login)
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_github.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/github.py tests/test_github.py
git commit -m "feat: add discover_slash_command_candidates to GitHubClient"
```

---

### Task 7: Handle slash command trigger in processor

**Files:**
- Modify: `src/pr_reviewer/processor.py`
- Test: `tests/test_slash_command.py`

**Step 1: Write the failing test**

Add to `tests/test_slash_command.py`:

```python
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pr_reviewer.config import AppConfig
from pr_reviewer.github import GitHubClient
from pr_reviewer.models import PRCandidate, ProcessedState, ReviewerOutput, SlashCommandTrigger
from pr_reviewer.processor import process_candidate


class DummyStore:
    def __init__(self, state: ProcessedState | None = None) -> None:
        self.state = state or ProcessedState()
        self.saved = False

    def get(self, _key):
        return self.state

    def set(self, _key, state):
        self.state = state

    def save(self) -> None:
        self.saved = True


class DummyWorkspace:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    def prepare(self, _pr):
        return self.workdir

    def update_to_latest(self, _workdir, pr):
        pass

    def cleanup(self, _workdir):
        return None


def _sample_pr_with_slash_command(*, force: bool = False) -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
        additions=20,
        deletions=5,
        changed_file_paths=["src/app.py"],
        slash_command_trigger=SlashCommandTrigger(
            comment_id=123456,
            comment_author="alice",
            comment_created_at="2026-03-05T10:05:00+00:00",
            force=force,
        ),
    )


def test_slash_command_triggers_review(monkeypatch, tmp_path) -> None:
    store = DummyStore()
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    reactions: list[tuple[str, str, int, str]] = []
    monkeypatch.setattr(
        GitHubClient,
        "add_reaction_to_comment",
        lambda _self, owner, repo, cid, reaction: reactions.append((owner, repo, cid, reaction)),
    )
    posted_comments: list[str] = []
    monkeypatch.setattr(
        GitHubClient,
        "post_pr_comment_inline",
        lambda _self, _pr, body: posted_comments.append(body),
    )
    monkeypatch.setattr(GitHubClient, "add_eyes_reaction", lambda _self, _pr: None)

    now = datetime.now(UTC)
    ok_output = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="### Findings\n- No findings.\n\n### Test Gaps\n- None.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    async def fake_codex(_pr, _workdir, _timeout, *, model=None, reasoning_effort=None):
        return ok_output

    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", fake_codex)
    monkeypatch.setattr(
        "pr_reviewer.processor.write_review_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.md",
    )
    monkeypatch.setattr(
        "pr_reviewer.processor.write_reviewer_sidecar_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.raw.md",
    )

    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    changed = asyncio.run(
        process_candidate(cfg, client, store, workspace, _sample_pr_with_slash_command())
    )

    assert changed is True
    assert store.state.last_slash_command_id == 123456
    assert ("polymerdao", "obul", 123456, "eyes") in reactions
    assert any("starting review" in c.lower() for c in posted_comments)


def test_slash_command_skips_when_already_reviewed_at_head(monkeypatch, tmp_path) -> None:
    store = DummyStore(
        ProcessedState(
            last_processed_at="2026-03-05T09:00:00+00:00",
            last_reviewed_head_sha="deadbeef",
            last_status="posted",
        )
    )
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    reactions: list[tuple] = []
    monkeypatch.setattr(
        GitHubClient,
        "add_reaction_to_comment",
        lambda _self, owner, repo, cid, reaction: reactions.append((owner, repo, cid, reaction)),
    )
    posted_comments: list[str] = []
    monkeypatch.setattr(
        GitHubClient,
        "post_pr_comment_inline",
        lambda _self, _pr, body: posted_comments.append(body),
    )

    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    changed = asyncio.run(
        process_candidate(cfg, client, store, workspace, _sample_pr_with_slash_command(force=False))
    )

    assert changed is False
    assert any("already reviewed" in c.lower() for c in posted_comments)
    assert store.state.last_slash_command_id == 123456


def test_slash_command_force_reviews_even_when_already_reviewed(monkeypatch, tmp_path) -> None:
    store = DummyStore(
        ProcessedState(
            last_processed_at="2026-03-05T09:00:00+00:00",
            last_reviewed_head_sha="deadbeef",
            last_status="posted",
        )
    )
    workspace = DummyWorkspace(tmp_path)
    client = GitHubClient(viewer_login="Inkvi")

    monkeypatch.setattr(
        GitHubClient,
        "add_reaction_to_comment",
        lambda _self, *_args: None,
    )
    monkeypatch.setattr(
        GitHubClient,
        "post_pr_comment_inline",
        lambda _self, _pr, _body: None,
    )
    monkeypatch.setattr(GitHubClient, "add_eyes_reaction", lambda _self, _pr: None)

    now = datetime.now(UTC)
    ok_output = ReviewerOutput(
        reviewer="codex",
        status="ok",
        markdown="### Findings\n- No findings.\n\n### Test Gaps\n- None.",
        stdout="",
        stderr="",
        error=None,
        started_at=now,
        ended_at=now,
    )

    async def fake_codex(_pr, _workdir, _timeout, *, model=None, reasoning_effort=None):
        return ok_output

    monkeypatch.setattr("pr_reviewer.processor.run_codex_review", fake_codex)
    monkeypatch.setattr(
        "pr_reviewer.processor.write_review_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.md",
    )
    monkeypatch.setattr(
        "pr_reviewer.processor.write_reviewer_sidecar_markdown",
        lambda *_args, **_kwargs: tmp_path / "out.raw.md",
    )

    cfg = AppConfig(github_orgs=["polymerdao"], enabled_reviewers=["codex"])
    changed = asyncio.run(
        process_candidate(cfg, client, store, workspace, _sample_pr_with_slash_command(force=True))
    )

    assert changed is True
    assert store.state.last_status == "generated"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_slash_command.py::test_slash_command_triggers_review -v`
Expected: FAIL (slash command path not implemented in processor)

**Step 3: Write minimal implementation**

In `src/pr_reviewer/processor.py`, modify `process_candidate` function. After the `skip_reason` check and before the existing `decision = _compute_processing_decision(...)` block, add the slash command path:

```python
# Slash command trigger path — bypasses re-request logic.
if pr.slash_command_trigger is not None:
    trigger = pr.slash_command_trigger

    # React to the comment and reply.
    try:
        client.add_reaction_to_comment(pr.owner, pr.repo, trigger.comment_id, "eyes")
    except Exception as exc:  # noqa: BLE001
        warn(f"{pr.key}: failed to react to /review comment: {exc}")

    # Check if already reviewed at this HEAD.
    if (
        not trigger.force
        and previous.last_reviewed_head_sha == pr.head_sha
        and previous.last_status in ("posted", "approved", "changes_requested", "generated")
    ):
        try:
            client.post_pr_comment_inline(
                pr,
                "Already reviewed at this commit. Push new changes or use "
                "`/review force` to re-review.",
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"{pr.key}: failed to post already-reviewed reply: {exc}")

        previous.last_slash_command_id = trigger.comment_id
        store.set(pr.key, previous)
        store.save()
        return False

    try:
        client.post_pr_comment_inline(pr, "Starting review of the latest changes…")
    except Exception as exc:  # noqa: BLE001
        warn(f"{pr.key}: failed to post starting-review comment: {exc}")

    # Fall through to the review pipeline below (skip re-request decision).
else:
    # Existing re-request trigger logic.
    decision = _compute_processing_decision(previous, pr, config.trigger_mode)
    if decision.should_process:
        detail(f"trigger check passed ({decision.reason}) {pr.url}")
    else:
        detail(f"skipping, trigger check skipped ({decision.reason}) {pr.url}")
        previous.last_status = f"skipped_{decision.reason}"
        previous.trigger_mode = config.trigger_mode
        store.set(pr.key, previous)
        store.save()
        return False

    try:
        client.add_eyes_reaction(pr)
    except Exception as exc:  # noqa: BLE001
        warn(f"{pr.key}: failed to add eyes reaction: {exc}")

    if decision.reason == "new_rerequest" and config.post_rerequest_comment:
        try:
            client.post_pr_comment_inline(
                pr,
                "Starting review of the latest changes…",
            )
        except Exception as exc:  # noqa: BLE001
            warn(f"{pr.key}: failed to post rerequest comment: {exc}")
```

Also, in `_publish_and_persist`, update to persist `last_slash_command_id`:

```python
last_slash_command_id = previous.last_slash_command_id
if pr.slash_command_trigger is not None:
    last_slash_command_id = pr.slash_command_trigger.comment_id
```

And include it in the `ProcessedState(...)` constructor call.

**Step 4: Run tests**

Run: `uv run pytest tests/test_slash_command.py tests/test_processor.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/processor.py tests/test_slash_command.py
git commit -m "feat: handle slash command trigger in processor"
```

---

### Task 8: Wire slash command discovery into daemon poll cycle

**Files:**
- Modify: `src/pr_reviewer/daemon.py`
- Test: `tests/test_daemon.py`

**Step 1: Write the failing test**

Add to `tests/test_daemon.py`:

```python
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from pr_reviewer.config import AppConfig
from pr_reviewer.daemon import run_cycle
from pr_reviewer.models import PRCandidate, SlashCommandTrigger
from pr_reviewer.preflight import PreflightResult
from pr_reviewer.state import StateStore


def test_run_cycle_merges_slash_command_candidates(monkeypatch, tmp_path) -> None:
    config = AppConfig(github_orgs=["polymerdao"], slash_command_enabled=True)
    preflight = PreflightResult(viewer_login="Inkvi")
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)
    store._owns_lock = True
    store.load()

    reviewer_assigned_pr = PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="assigned pr",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-03-05T10:00:00Z",
        additions=20,
        deletions=5,
        changed_file_paths=["src/app.py"],
    )

    slash_pr = PRCandidate(
        owner="polymerdao",
        repo="bridge",
        number=10,
        url="https://github.com/polymerdao/bridge/pull/10",
        title="slash pr",
        author_login="bob",
        base_ref="main",
        head_sha="cafe1234",
        updated_at="2026-03-05T10:01:00Z",
        additions=15,
        deletions=3,
        changed_file_paths=["src/main.py"],
        slash_command_trigger=SlashCommandTrigger(
            comment_id=999,
            comment_author="bob",
            comment_created_at="2026-03-05T10:01:00+00:00",
        ),
    )

    from pr_reviewer.github import GitHubClient
    monkeypatch.setattr(
        GitHubClient,
        "discover_pr_candidates",
        lambda _self, _config: [reviewer_assigned_pr],
    )
    monkeypatch.setattr(
        GitHubClient,
        "discover_slash_command_candidates",
        lambda _self, _config, _store: [slash_pr],
    )

    processed_keys: list[str] = []

    async def fake_process(_config, _client, _store, _workspace, pr, **_kwargs):
        processed_keys.append(pr.key)
        return True

    monkeypatch.setattr("pr_reviewer.daemon.process_candidate", fake_process)

    processed = asyncio.run(run_cycle(config, preflight, store))

    assert processed == 2
    assert "polymerdao/obul#64" in processed_keys
    assert "polymerdao/bridge#10" in processed_keys
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon.py::test_run_cycle_merges_slash_command_candidates -v`
Expected: FAIL

**Step 3: Write minimal implementation**

In `src/pr_reviewer/daemon.py`, modify `run_cycle`:

After `candidates = client.discover_pr_candidates(config)`, add:

```python
# Discover slash-command-triggered PRs and merge.
if config.slash_command_enabled:
    try:
        slash_candidates = client.discover_slash_command_candidates(config, store)
    except Exception as exc:  # noqa: BLE001
        warn(f"Failed to discover slash command PRs: {exc}")
        slash_candidates = []

    # Merge: slash command candidates take priority (they carry trigger metadata).
    existing_keys = {pr.key.lower() for pr in candidates}
    for sc in slash_candidates:
        if sc.key.lower() not in existing_keys:
            candidates.append(sc)
        else:
            # Replace existing candidate with slash-command version.
            candidates = [
                sc if c.key.lower() == sc.key.lower() else c for c in candidates
            ]
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/pr_reviewer/daemon.py tests/test_daemon.py
git commit -m "feat: merge slash command candidates into daemon poll cycle"
```

---

### Task 9: Add CLI option and config example

**Files:**
- Modify: `src/pr_reviewer/cli.py`
- Modify: `config.example.toml`

**Step 1: Add CLI option**

In `src/pr_reviewer/cli.py`, add after existing option type aliases:

```python
SlashCommandEnabledOption = Annotated[
    bool | None,
    typer.Option(
        "--slash-command-enabled/--no-slash-command-enabled",
        help="Override slash_command_enabled from config.",
    ),
]
```

Add the parameter to `run_once_command`, `start_command`, and `check_command` signatures. Wire it through `_load_runtime` using `_apply_bool_override`.

Also add `"Slash command enabled"` row to the check command table.

**Step 2: Update config.example.toml**

Read it first, then add:

```toml
# Slash command trigger — respond to /review comments in monitored org PRs
slash_command_enabled = true
```

**Step 3: Run all tests**

Run: `uv run pytest -v`
Expected: ALL PASS

**Step 4: Run linting**

Run: `uv run ruff check . && uv run ruff format .`

**Step 5: Commit**

```bash
git add src/pr_reviewer/cli.py config.example.toml
git commit -m "feat: add --slash-command-enabled CLI option and config example"
```

---

### Task 10: Update README

**Files:**
- Modify: `README.md`

**Step 1: Add documentation**

Add a section about slash command triggers after the existing "Behavior" section. Document:
- `/review` and `/review force` commands
- Who can trigger (PR author + org members)
- The `slash_command_enabled` config option
- CLI override flag

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document slash command trigger feature"
```

---

### Task 11: Final integration test

**Files:**
- Test: `tests/test_slash_command.py`

**Step 1: Add end-to-end integration test**

Write a test that exercises the full flow: discovery → processing → posting, using monkeypatched GitHubClient methods. Verify that the state is correctly updated with `last_slash_command_id` and that the correct GitHub API calls are made in the right order (react → reply → review → post).

**Step 2: Run all tests**

Run: `uv run pytest -v`
Expected: ALL PASS

**Step 3: Run linting**

Run: `uv run ruff check . && uv run ruff format .`

**Step 4: Commit**

```bash
git add tests/test_slash_command.py
git commit -m "test: add integration test for slash command flow"
```
