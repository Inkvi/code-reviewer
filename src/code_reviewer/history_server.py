"""PR review history API server.

Serves a read-only JSON API over review artifacts stored in the ``reviews/``
directory, and optionally serves a built React frontend as static files.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route

from code_reviewer.review_decision import infer_review_decision

logger = logging.getLogger(__name__)

KNOWN_STAGES = (
    "lightweight",
    "claude",
    "codex",
    "gemini",
    "reconcile",
    "triage.prompt",
    "lightweight.prompt",
    "claude.prompt",
    "codex.prompt",
    "gemini.prompt",
    "reconcile.prompt",
)
_VERSION_RE = re.compile(r"^(\d{8}T\d{6}Z)-([a-f0-9]+)$")


def _read_meta(path: Path) -> dict[str, Any]:
    """Read a meta.json file, returning an empty dict on any failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _read_conversation_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a conversation JSONL file, returning a list of events."""
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass
    return events


# ---------------------------------------------------------------------------
# Filesystem scanning helpers
# ---------------------------------------------------------------------------


def list_repos(reviews_dir: Path) -> list[dict[str, Any]]:
    """Return [{org, repo}] for every org/repo that has review artifacts."""
    repos: list[dict[str, Any]] = []
    if not reviews_dir.is_dir():
        return repos
    for org_dir in sorted(reviews_dir.iterdir()):
        if not org_dir.is_dir() or org_dir.name == "local":
            continue
        for repo_dir in sorted(org_dir.iterdir()):
            if not repo_dir.is_dir():
                continue
            pr_count = sum(
                1 for f in repo_dir.iterdir() if f.is_file() and re.match(r"^pr-\d+\.md$", f.name)
            )
            repos.append({"org": org_dir.name, "repo": repo_dir.name, "pr_count": pr_count})
    return repos


def _detect_stages(repo_dir: Path, number: int) -> list[str]:
    """Return list of stage names that have artifacts for a PR."""
    stages: list[str] = []
    for stage in KNOWN_STAGES:
        if (repo_dir / f"pr-{number}.{stage}.md").is_file():
            stages.append(stage)
    return stages


def _detect_review_type(stages: list[str]) -> str:
    if "lightweight" in stages:
        return "lightweight"
    if any(s in stages for s in ("claude", "codex", "gemini")):
        return "full"
    return "unknown"


def _resolve_repo_dir(reviews_dir: Path, org: str, repo: str) -> Path | None:
    """Resolve an org/repo path and reject traversal outside the reviews root."""
    try:
        reviews_root = reviews_dir.resolve()
        repo_dir = (reviews_dir / org / repo).resolve()
    except OSError:
        return None
    if not repo_dir.is_relative_to(reviews_root):
        return None
    return repo_dir


def _iter_pr_numbers(repo_dir: Path) -> list[int]:
    """Return PR numbers sorted numerically for stable history browsing."""
    pr_numbers: list[int] = []
    for file_path in repo_dir.iterdir():
        if not file_path.is_file():
            continue
        match = re.match(r"^pr-(\d+)\.md$", file_path.name)
        if match:
            pr_numbers.append(int(match.group(1)))
    return sorted(pr_numbers)


def _pr_summary(repo_dir: Path, number: int) -> dict[str, Any]:
    """Build summary dict for a single PR."""
    final_path = repo_dir / f"pr-{number}.md"
    final_review = final_path.read_text(encoding="utf-8") if final_path.is_file() else None
    stages = _detect_stages(repo_dir, number)
    review_type = _detect_review_type(stages)
    decision = infer_review_decision(final_review) if final_review else None
    meta = _read_meta(repo_dir / f"pr-{number}.meta.json")
    history_dir = repo_dir / f"pr-{number}"
    version_count = 0
    if history_dir.is_dir():
        version_count = sum(
            1
            for f in history_dir.iterdir()
            if f.is_file() and f.suffix == ".md" and "." not in f.stem.split("-", 2)[-1]
        )
    return {
        "number": number,
        "review_type": review_type,
        "decision": decision,
        "stages": stages,
        "version_count": version_count,
        "author": meta.get("author"),
        "title": meta.get("title"),
        "meta": meta or None,
    }


def list_prs(reviews_dir: Path, org: str, repo: str) -> list[dict[str, Any]]:
    """Return PR summaries for a given org/repo."""
    repo_dir = _resolve_repo_dir(reviews_dir, org, repo)
    if repo_dir is None or not repo_dir.is_dir():
        return []
    return [_pr_summary(repo_dir, number) for number in _iter_pr_numbers(repo_dir)]


def get_pr_detail(reviews_dir: Path, org: str, repo: str, number: int) -> dict[str, Any] | None:
    """Return full PR detail including stage contents."""
    repo_dir = _resolve_repo_dir(reviews_dir, org, repo)
    if repo_dir is None:
        return None
    final_path = repo_dir / f"pr-{number}.md"
    if not final_path.is_file():
        return None
    final_review = final_path.read_text(encoding="utf-8")
    stages = _detect_stages(repo_dir, number)
    review_type = _detect_review_type(stages)
    decision = infer_review_decision(final_review)
    stage_contents: dict[str, str] = {}
    stage_conversations: dict[str, list[dict[str, Any]]] = {}
    for stage in stages:
        stage_path = repo_dir / f"pr-{number}.{stage}.md"
        if stage_path.is_file():
            stage_contents[stage] = stage_path.read_text(encoding="utf-8")
        conv = _read_conversation_jsonl(repo_dir / f"pr-{number}.{stage}.conversation.jsonl")
        if conv:
            stage_conversations[stage] = conv
    meta = _read_meta(repo_dir / f"pr-{number}.meta.json")
    history_dir = repo_dir / f"pr-{number}"
    versions = _list_versions(history_dir) if history_dir.is_dir() else []
    return {
        "number": number,
        "org": org,
        "repo": repo,
        "review_type": review_type,
        "decision": decision,
        "final_review": final_review,
        "stages": stages,
        "stage_contents": stage_contents,
        "stage_conversations": stage_conversations or None,
        "versions": versions,
        "author": meta.get("author"),
        "title": meta.get("title"),
        "meta": meta or None,
    }


def get_pr_history(reviews_dir: Path, org: str, repo: str, number: int) -> list[dict[str, Any]]:
    """Return historical versions for a PR."""
    repo_dir = _resolve_repo_dir(reviews_dir, org, repo)
    if repo_dir is None:
        return []
    history_dir = repo_dir / f"pr-{number}"
    return _list_versions(history_dir)


def _parse_version_stem(stem: str) -> tuple[str, str] | None:
    """Parse a version stem like '20260318T120530Z-abc12345abcd' into (timestamp, sha)."""
    m = _VERSION_RE.match(stem)
    return (m.group(1), m.group(2)) if m else None


def _list_versions(history_dir: Path) -> list[dict[str, Any]]:
    """List version snapshots in a PR history directory."""
    if not history_dir.is_dir():
        return []
    version_map: dict[str, dict[str, Any]] = {}
    for f in sorted(history_dir.iterdir()):
        if not f.is_file() or f.suffix != ".md":
            continue
        name = f.stem
        parts = name.split(".", 1)
        base_stem = parts[0]
        stage = parts[1] if len(parts) > 1 else None
        parsed = _parse_version_stem(base_stem)
        if not parsed:
            continue
        timestamp, sha = parsed
        version_key = f"{timestamp}-{sha}"
        if version_key not in version_map:
            version_map[version_key] = {
                "version": version_key,
                "timestamp": timestamp,
                "sha": sha,
                "stages": [],
                "has_final": False,
            }
        if stage is None:
            version_map[version_key]["has_final"] = True
        else:
            version_map[version_key]["stages"].append(stage)
    return sorted(version_map.values(), key=lambda v: v["timestamp"], reverse=True)


def get_version_detail(
    reviews_dir: Path, org: str, repo: str, number: int, version: str
) -> dict[str, Any] | None:
    """Return contents for a specific historical version."""
    repo_dir = _resolve_repo_dir(reviews_dir, org, repo)
    if repo_dir is None:
        return None
    history_dir = repo_dir / f"pr-{number}"
    if not history_dir.is_dir():
        return None
    final_path = history_dir / f"{version}.md"
    final_review = final_path.read_text(encoding="utf-8") if final_path.is_file() else None
    if final_review is None:
        return None
    stage_contents: dict[str, str] = {}
    stage_conversations: dict[str, list[dict[str, Any]]] = {}
    stages: list[str] = []
    for stage in KNOWN_STAGES:
        stage_path = history_dir / f"{version}.{stage}.md"
        if stage_path.is_file():
            stages.append(stage)
            stage_contents[stage] = stage_path.read_text(encoding="utf-8")
        conv = _read_conversation_jsonl(history_dir / f"{version}.{stage}.conversation.jsonl")
        if conv:
            stage_conversations[stage] = conv
    meta = _read_meta(history_dir / f"{version}.meta.json")
    parsed = _parse_version_stem(version)
    return {
        "version": version,
        "timestamp": parsed[0] if parsed else version,
        "sha": parsed[1] if parsed else "",
        "final_review": final_review,
        "stages": stages,
        "stage_contents": stage_contents,
        "stage_conversations": stage_conversations or None,
        "author": meta.get("author"),
        "title": meta.get("title"),
        "meta": meta or None,
        "decision": infer_review_decision(final_review),
        "review_type": _detect_review_type(stages),
    }


def get_stage_content(
    reviews_dir: Path, org: str, repo: str, number: int, stage: str
) -> str | None:
    """Return content for a specific stage of the latest review."""
    if stage not in KNOWN_STAGES:
        return None
    repo_dir = _resolve_repo_dir(reviews_dir, org, repo)
    if repo_dir is None:
        return None
    stage_path = repo_dir / f"pr-{number}.{stage}.md"
    if not stage_path.is_file():
        return None
    return stage_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HTTP server (Starlette ASGI)
# ---------------------------------------------------------------------------


def _json(data: Any, status: int = 200) -> Response:
    return JSONResponse(data, status_code=status)


def create_history_app(
    *,
    reviews_dir: Path = Path("./reviews"),
    static_dir: Path | None = None,
    enable_cors: bool = False,
) -> Starlette:
    """Create a Starlette ASGI app for the history API."""

    async def healthz(request: Request) -> Response:
        return _json({"status": "ok"})

    async def api_repos(request: Request) -> Response:
        return _json(list_repos(reviews_dir))

    async def api_prs(request: Request) -> Response:
        org = request.path_params["org"]
        repo = request.path_params["repo"]
        return _json(list_prs(reviews_dir, org, repo))

    async def api_pr_detail(request: Request) -> Response:
        org = request.path_params["org"]
        repo = request.path_params["repo"]
        number = int(request.path_params["number"])
        result = get_pr_detail(reviews_dir, org, repo, number)
        if result is None:
            return _json({"error": "not found"}, 404)
        return _json(result)

    async def api_pr_history(request: Request) -> Response:
        org = request.path_params["org"]
        repo = request.path_params["repo"]
        number = int(request.path_params["number"])
        return _json(get_pr_history(reviews_dir, org, repo, number))

    async def api_version_detail(request: Request) -> Response:
        org = request.path_params["org"]
        repo = request.path_params["repo"]
        number = int(request.path_params["number"])
        version = request.path_params["version"]
        result = get_version_detail(reviews_dir, org, repo, number, version)
        if result is None:
            return _json({"error": "not found"}, 404)
        return _json(result)

    async def api_stage_content(request: Request) -> Response:
        org = request.path_params["org"]
        repo = request.path_params["repo"]
        number = int(request.path_params["number"])
        stage = request.path_params["stage"]
        content = get_stage_content(reviews_dir, org, repo, number, stage)
        if content is None:
            return _json({"error": "not found"}, 404)
        return _json({"stage": stage, "content": content})

    async def spa_fallback(request: Request) -> Response:
        if static_dir is None:
            return _json({"error": "not found"}, 404)
        # Try to serve the exact file first
        rel = request.path_params.get("path", "").lstrip("/")
        if rel:
            file_path = (static_dir / rel).resolve()
            if not file_path.is_relative_to(static_dir.resolve()):
                return _json({"error": "not found"}, 404)
            if file_path.is_file():
                import mimetypes

                content_type, _ = mimetypes.guess_type(str(file_path))
                from starlette.responses import FileResponse

                return FileResponse(str(file_path), media_type=content_type)
        # SPA fallback: serve index.html for client-side routing
        index = static_dir / "index.html"
        if index.is_file():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        return _json({"error": "not found"}, 404)

    routes: list[Route | Mount] = [
        Route("/healthz", healthz),
        Route("/api/repos", api_repos),
        Route("/api/repos/{org}/{repo}/prs", api_prs),
        Route("/api/repos/{org}/{repo}/prs/{number:int}", api_pr_detail),
        Route("/api/repos/{org}/{repo}/prs/{number:int}/history", api_pr_history),
        Route("/api/repos/{org}/{repo}/prs/{number:int}/history/{version}", api_version_detail),
        Route("/api/repos/{org}/{repo}/prs/{number:int}/stages/{stage}", api_stage_content),
    ]

    # SPA fallback handles both static file serving and client-side routing
    routes.append(Route("/{path:path}", spa_fallback))
    routes.append(Route("/", spa_fallback))

    middleware = []
    if enable_cors:
        from starlette.middleware.cors import CORSMiddleware

        middleware.append(
            Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "OPTIONS"])
        )

    return Starlette(routes=routes, middleware=middleware)
