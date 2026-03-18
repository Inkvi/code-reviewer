"""PR review history API server.

Serves a read-only JSON API over review artifacts stored in the ``reviews/``
directory, and optionally serves a built React frontend as static files.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from code_reviewer.review_decision import infer_review_decision

logger = logging.getLogger(__name__)

KNOWN_STAGES = ("lightweight", "claude", "codex", "gemini", "reconcile")
_VERSION_RE = re.compile(r"^(\d{8}T\d{6}Z)-([a-f0-9]+)$")


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


def _pr_summary(repo_dir: Path, number: int) -> dict[str, Any]:
    """Build summary dict for a single PR."""
    final_path = repo_dir / f"pr-{number}.md"
    final_review = final_path.read_text(encoding="utf-8") if final_path.is_file() else None
    stages = _detect_stages(repo_dir, number)
    review_type = _detect_review_type(stages)
    decision = infer_review_decision(final_review) if final_review else None
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
    }


def list_prs(reviews_dir: Path, org: str, repo: str) -> list[dict[str, Any]]:
    """Return PR summaries for a given org/repo."""
    repo_dir = reviews_dir / org / repo
    if not repo_dir.is_dir():
        return []
    prs: list[dict[str, Any]] = []
    for f in sorted(repo_dir.iterdir()):
        m = re.match(r"^pr-(\d+)\.md$", f.name)
        if m and f.is_file():
            prs.append(_pr_summary(repo_dir, int(m.group(1))))
    return prs


def get_pr_detail(reviews_dir: Path, org: str, repo: str, number: int) -> dict[str, Any] | None:
    """Return full PR detail including stage contents."""
    repo_dir = reviews_dir / org / repo
    final_path = repo_dir / f"pr-{number}.md"
    if not final_path.is_file():
        return None
    final_review = final_path.read_text(encoding="utf-8")
    stages = _detect_stages(repo_dir, number)
    review_type = _detect_review_type(stages)
    decision = infer_review_decision(final_review)
    stage_contents: dict[str, str] = {}
    for stage in stages:
        stage_path = repo_dir / f"pr-{number}.{stage}.md"
        if stage_path.is_file():
            stage_contents[stage] = stage_path.read_text(encoding="utf-8")
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
        "versions": versions,
    }


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
    history_dir = reviews_dir / org / repo / f"pr-{number}"
    if not history_dir.is_dir():
        return None
    final_path = history_dir / f"{version}.md"
    final_review = final_path.read_text(encoding="utf-8") if final_path.is_file() else None
    if final_review is None:
        return None
    stage_contents: dict[str, str] = {}
    stages: list[str] = []
    for stage in KNOWN_STAGES:
        stage_path = history_dir / f"{version}.{stage}.md"
        if stage_path.is_file():
            stages.append(stage)
            stage_contents[stage] = stage_path.read_text(encoding="utf-8")
    parsed = _parse_version_stem(version)
    return {
        "version": version,
        "timestamp": parsed[0] if parsed else version,
        "sha": parsed[1] if parsed else "",
        "final_review": final_review,
        "stages": stages,
        "stage_contents": stage_contents,
        "decision": infer_review_decision(final_review),
        "review_type": _detect_review_type(stages),
    }


def get_stage_content(
    reviews_dir: Path, org: str, repo: str, number: int, stage: str
) -> str | None:
    """Return content for a specific stage of the latest review."""
    if stage not in KNOWN_STAGES:
        return None
    stage_path = reviews_dir / org / repo / f"pr-{number}.{stage}.md"
    if not stage_path.is_file():
        return None
    return stage_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

_API_ROUTES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^/api/repos$"), "repos"),
    (re.compile(r"^/api/repos/([^/]+)/([^/]+)/prs$"), "prs"),
    (re.compile(r"^/api/repos/([^/]+)/([^/]+)/prs/(\d+)$"), "pr_detail"),
    (re.compile(r"^/api/repos/([^/]+)/([^/]+)/prs/(\d+)/history$"), "pr_history"),
    (re.compile(r"^/api/repos/([^/]+)/([^/]+)/prs/(\d+)/history/([^/]+)$"), "version_detail"),
    (re.compile(r"^/api/repos/([^/]+)/([^/]+)/prs/(\d+)/stages/([^/]+)$"), "stage_content"),
]


def _make_handler(
    reviews_dir: Path,
    static_dir: Path | None,
    enable_cors: bool = False,
) -> type[BaseHTTPRequestHandler]:
    """Create a request handler class bound to the given config."""

    class HistoryHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]

            if path == "/healthz":
                self._json_response(HTTPStatus.OK, {"status": "ok"})
                return

            for pattern, route_name in _API_ROUTES:
                m = pattern.match(path)
                if m:
                    self._handle_api(route_name, m.groups())
                    return

            if static_dir:
                self._serve_static(path)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_OPTIONS(self) -> None:  # noqa: N802
            if enable_cors:
                self.send_response(HTTPStatus.NO_CONTENT)
                self._add_cors_headers()
                self.end_headers()
            else:
                self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

        def _handle_api(self, route: str, groups: tuple[str, ...]) -> None:
            if route == "repos":
                data = list_repos(reviews_dir)
            elif route == "prs":
                org, repo = groups[0], groups[1]
                data = list_prs(reviews_dir, org, repo)
            elif route == "pr_detail":
                org, repo, num = groups[0], groups[1], int(groups[2])
                result = get_pr_detail(reviews_dir, org, repo, num)
                if result is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                data = result
            elif route == "pr_history":
                org, repo, num = groups[0], groups[1], int(groups[2])
                history_dir = reviews_dir / org / repo / f"pr-{num}"
                data = _list_versions(history_dir)
            elif route == "version_detail":
                org, repo, num, version = groups[0], groups[1], int(groups[2]), groups[3]
                result = get_version_detail(reviews_dir, org, repo, num, version)
                if result is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                data = result
            elif route == "stage_content":
                org, repo, num, stage = groups[0], groups[1], int(groups[2]), groups[3]
                content = get_stage_content(reviews_dir, org, repo, num, stage)
                if content is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                data = {"stage": stage, "content": content}
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._json_response(HTTPStatus.OK, data)

        def _serve_static(self, path: str) -> None:
            assert static_dir is not None
            if path == "/":
                path = "/index.html"
            file_path = static_dir / path.lstrip("/")
            try:
                file_path = file_path.resolve()
                if not file_path.is_relative_to(static_dir.resolve()):
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
            except (ValueError, OSError):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            if file_path.is_file():
                content_type, _ = mimetypes.guess_type(str(file_path))
                body = file_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                # SPA fallback: serve index.html for client-side routing
                index_path = static_dir / "index.html"
                if index_path.is_file():
                    body = index_path.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)

        def _json_response(self, status: HTTPStatus, data: Any) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if enable_cors:
                self._add_cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def _add_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.info(format, *args)

    return HistoryHandler


def run_history_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    reviews_dir: Path = Path("./reviews"),
    static_dir: Path | None = None,
    enable_cors: bool = False,
) -> None:
    """Start the history API server (blocks forever)."""
    handler_cls = _make_handler(reviews_dir, static_dir, enable_cors)
    server = HTTPServer((host, port), handler_cls)
    logger.info("History server listening on %s:%d", host, port)
    logger.info("Reviews directory: %s", reviews_dir.resolve())
    if static_dir:
        logger.info("Serving static files from: %s", static_dir.resolve())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down history server")
    finally:
        server.server_close()
