from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from code_reviewer.history_server import (
    create_history_app,
    get_pr_detail,
    get_pr_history,
    get_stage_content,
    get_version_detail,
    list_prs,
    list_repos,
)


def _setup_reviews(tmp_path: Path) -> Path:
    """Create a sample reviews directory structure."""
    reviews = tmp_path / "reviews"
    repo_dir = reviews / "myorg" / "myrepo"
    repo_dir.mkdir(parents=True)

    # PR with lightweight review
    (repo_dir / "pr-1.md").write_text("All good, no issues.\n")
    (repo_dir / "pr-1.lightweight.md").write_text("Lightweight checklist passed.\n")

    # PR with full review (P1 finding -> request_changes)
    (repo_dir / "pr-2.md").write_text("[P1] Security issue found.\n")
    (repo_dir / "pr-2.claude.md").write_text("Claude: [P1] Security issue.\n")
    (repo_dir / "pr-2.codex.md").write_text("Codex: Looks fine.\n")
    (repo_dir / "pr-2.reconcile.md").write_text("[P1] Security issue found.\n")
    (repo_dir / "pr-2.triage.prompt.md").write_text("## Prompt\nClassify this PR.\n")
    (repo_dir / "pr-2.claude.prompt.md").write_text("## Prompt\nReview this PR.\n")
    (repo_dir / "pr-2.reconcile.prompt.md").write_text("## Prompt\nReconcile these reviews.\n")

    # Double-digit PR number to verify numeric ordering in the UI/API.
    (repo_dir / "pr-10.md").write_text("Needs follow-up.\n")

    # Version history for PR 2
    history_dir = repo_dir / "pr-2"
    history_dir.mkdir()
    (history_dir / "20260318T120000Z-abc123456789.md").write_text("First review.\n")
    (history_dir / "20260318T120000Z-abc123456789.claude.md").write_text("Claude v1.\n")
    (history_dir / "20260318T130000Z-def987654321.md").write_text("[P1] Security issue.\n")
    (history_dir / "20260318T130000Z-def987654321.claude.md").write_text("Claude v2.\n")
    (history_dir / "20260318T130000Z-def987654321.reconcile.md").write_text("Reconciled v2.\n")
    (history_dir / "20260318T130000Z-def987654321.triage.prompt.md").write_text(
        "## Prompt\nClassify v2.\n"
    )
    (history_dir / "20260318T130000Z-def987654321.claude.prompt.md").write_text(
        "## Prompt\nReview v2.\n"
    )

    # Second repo with one PR
    repo2_dir = reviews / "myorg" / "other-repo"
    repo2_dir.mkdir(parents=True)
    (repo2_dir / "pr-5.md").write_text("LGTM.\n")

    return reviews


def test_list_repos(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    repos = list_repos(reviews)
    assert len(repos) == 2
    assert repos[0] == {"org": "myorg", "repo": "myrepo", "pr_count": 3}
    assert repos[1] == {"org": "myorg", "repo": "other-repo", "pr_count": 1}


def test_list_repos_empty(tmp_path: Path) -> None:
    repos = list_repos(tmp_path / "nonexistent")
    assert repos == []


def test_list_repos_skips_local(tmp_path: Path) -> None:
    reviews = tmp_path / "reviews"
    (reviews / "local" / "somerepo").mkdir(parents=True)
    (reviews / "realorg" / "realrepo").mkdir(parents=True)
    (reviews / "realorg" / "realrepo" / "pr-1.md").write_text("review\n")
    repos = list_repos(reviews)
    assert len(repos) == 1
    assert repos[0]["org"] == "realorg"


def test_list_prs(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    prs = list_prs(reviews, "myorg", "myrepo")
    assert [pr["number"] for pr in prs] == [1, 2, 10]

    pr1 = prs[0]
    assert pr1["number"] == 1
    assert pr1["review_type"] == "lightweight"
    assert pr1["decision"] == "approve"
    assert pr1["stages"] == ["lightweight"]

    pr2 = prs[1]
    assert pr2["number"] == 2
    assert pr2["review_type"] == "full"
    assert pr2["decision"] == "request_changes"
    assert "claude" in pr2["stages"]
    assert "codex" in pr2["stages"]
    assert "reconcile" in pr2["stages"]
    assert pr2["version_count"] == 2

    pr10 = prs[2]
    assert pr10["number"] == 10
    assert pr10["review_type"] == "unknown"
    assert pr10["decision"] == "approve"
    assert pr10["stages"] == []
    assert pr10["version_count"] == 0


def test_list_prs_nonexistent_repo(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    prs = list_prs(reviews, "nonexistent", "repo")
    assert prs == []


def test_get_pr_detail_lightweight(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    detail = get_pr_detail(reviews, "myorg", "myrepo", 1)
    assert detail is not None
    assert detail["number"] == 1
    assert detail["review_type"] == "lightweight"
    assert detail["decision"] == "approve"
    assert "lightweight" in detail["stage_contents"]
    assert "Lightweight checklist" in detail["stage_contents"]["lightweight"]


def test_get_pr_detail_full(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    detail = get_pr_detail(reviews, "myorg", "myrepo", 2)
    assert detail is not None
    assert detail["review_type"] == "full"
    assert detail["decision"] == "request_changes"
    assert "claude" in detail["stage_contents"]
    assert "codex" in detail["stage_contents"]
    assert "reconcile" in detail["stage_contents"]
    assert len(detail["versions"]) == 2


def test_get_pr_detail_not_found(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    assert get_pr_detail(reviews, "myorg", "myrepo", 999) is None


def test_get_version_detail(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    v = get_version_detail(reviews, "myorg", "myrepo", 2, "20260318T130000Z-def987654321")
    assert v is not None
    assert v["timestamp"] == "20260318T130000Z"
    assert v["sha"] == "def987654321"
    assert v["decision"] == "request_changes"
    assert "claude" in v["stages"]
    assert "reconcile" in v["stages"]
    assert "Claude v2" in v["stage_contents"]["claude"]


def test_get_version_detail_not_found(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    assert get_version_detail(reviews, "myorg", "myrepo", 2, "nonexistent") is None


def test_get_pr_history(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    history = get_pr_history(reviews, "myorg", "myrepo", 2)
    assert len(history) == 2
    assert history[0]["version"] == "20260318T130000Z-def987654321"
    assert history[1]["version"] == "20260318T120000Z-abc123456789"


def test_get_stage_content(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    content = get_stage_content(reviews, "myorg", "myrepo", 2, "claude")
    assert content is not None
    assert "Security issue" in content


def test_get_stage_content_unknown_stage(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    assert get_stage_content(reviews, "myorg", "myrepo", 2, "unknown") is None


def test_get_stage_content_missing_file(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    assert get_stage_content(reviews, "myorg", "myrepo", 1, "claude") is None


def test_repo_lookup_rejects_path_traversal(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    escape_repo_dir = tmp_path / "escape"
    escape_repo_dir.mkdir()
    (escape_repo_dir / "pr-7.md").write_text("Escaped review.\n")
    (escape_repo_dir / "pr-7.claude.md").write_text("Escaped stage.\n")
    history_dir = escape_repo_dir / "pr-7"
    history_dir.mkdir()
    (history_dir / "20260318T140000Z-abc123456789.md").write_text("Escaped history.\n")

    assert list_prs(reviews, "..", "escape") == []
    assert get_pr_detail(reviews, "..", "escape", 7) is None
    assert get_pr_history(reviews, "..", "escape", 7) == []
    assert get_version_detail(reviews, "..", "escape", 7, "20260318T140000Z-abc123456789") is None
    assert get_stage_content(reviews, "..", "escape", 7, "claude") is None


# ---------------------------------------------------------------------------
# HTTP-level tests (Starlette TestClient)
# ---------------------------------------------------------------------------


def test_healthz_endpoint(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    app = create_history_app(reviews_dir=reviews)
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_api_repos_endpoint(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    app = create_history_app(reviews_dir=reviews)
    client = TestClient(app)
    resp = client.get("/api/repos")
    assert resp.status_code == 200
    repos = resp.json()
    assert len(repos) == 2
    assert repos[0]["org"] == "myorg"


def test_api_prs_endpoint(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    app = create_history_app(reviews_dir=reviews)
    client = TestClient(app)
    resp = client.get("/api/repos/myorg/myrepo/prs")
    assert resp.status_code == 200
    prs = resp.json()
    assert [pr["number"] for pr in prs] == [1, 2, 10]


def test_api_pr_detail_endpoint(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    app = create_history_app(reviews_dir=reviews)
    client = TestClient(app)
    resp = client.get("/api/repos/myorg/myrepo/prs/2")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["number"] == 2
    assert detail["review_type"] == "full"


def test_api_pr_detail_not_found(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    app = create_history_app(reviews_dir=reviews)
    client = TestClient(app)
    resp = client.get("/api/repos/myorg/myrepo/prs/999")
    assert resp.status_code == 404


def test_api_stage_content_endpoint(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    app = create_history_app(reviews_dir=reviews)
    client = TestClient(app)
    resp = client.get("/api/repos/myorg/myrepo/prs/2/stages/claude")
    assert resp.status_code == 200
    data = resp.json()
    assert "Security issue" in data["content"]


def test_api_history_endpoint(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    app = create_history_app(reviews_dir=reviews)
    client = TestClient(app)
    resp = client.get("/api/repos/myorg/myrepo/prs/2/history")
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) == 2


def test_api_version_detail_endpoint(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    app = create_history_app(reviews_dir=reviews)
    client = TestClient(app)
    resp = client.get("/api/repos/myorg/myrepo/prs/2/history/20260318T130000Z-def987654321")
    assert resp.status_code == 200
    v = resp.json()
    assert v["sha"] == "def987654321"


def test_static_spa_fallback(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>SPA</html>")
    app = create_history_app(reviews_dir=reviews, static_dir=static)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "SPA" in resp.text
    # SPA fallback for unknown route
    resp = client.get("/some/spa/route")
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_get_pr_detail_includes_prompt_stages(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    detail = get_pr_detail(reviews, "myorg", "myrepo", 2)
    assert detail is not None
    assert "triage.prompt" in detail["stages"]
    assert "claude.prompt" in detail["stages"]
    assert "reconcile.prompt" in detail["stages"]
    assert "Classify this PR" in detail["stage_contents"]["triage.prompt"]


def test_get_version_detail_includes_prompt_stages(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    v = get_version_detail(reviews, "myorg", "myrepo", 2, "20260318T130000Z-def987654321")
    assert v is not None
    assert "triage.prompt" in v["stages"]
    assert "claude.prompt" in v["stages"]
    assert "Classify v2" in v["stage_contents"]["triage.prompt"]


def test_get_stage_content_returns_prompt(tmp_path: Path) -> None:
    reviews = _setup_reviews(tmp_path)
    content = get_stage_content(reviews, "myorg", "myrepo", 2, "triage.prompt")
    assert content is not None
    assert "Classify this PR" in content
