# Embed History Web UI in Daemon — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the PR review history web UI from within the daemon process so it can be exposed via `kubectl port-forward`.

**Architecture:** When `--web-port` is passed to `code-reviewer start`, uvicorn runs as the main event loop. The daemon polling loop launches as a background `asyncio.create_task` via a Starlette lifespan. When `--web-port` is omitted, behavior is unchanged.

**Tech Stack:** Starlette (ASGI app), uvicorn (ASGI server), existing filesystem scanning helpers.

**Spec:** `docs/superpowers/specs/2026-03-17-embed-history-server-in-daemon-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `pyproject.toml` | Modify | Add `uvicorn` and `starlette` dependencies |
| `src/code_reviewer/history_server.py` | Rewrite | Replace stdlib HTTP server with Starlette ASGI app; keep all scanning helpers unchanged |
| `src/code_reviewer/daemon.py` | Modify | Add `create_daemon_app()` that wraps history app with daemon-as-background-task lifespan |
| `src/code_reviewer/cli.py` | Modify | Add `--web-port` to `start` command; migrate `history` command to uvicorn |
| `tests/test_history_server.py` | Modify | Replace raw function calls with Starlette `TestClient` for HTTP-level tests; keep data helper tests |

---

### Task 1: Add uvicorn and starlette dependencies

**Files:**
- Modify: `pyproject.toml:7-14`

- [ ] **Step 1: Add dependencies to pyproject.toml**

Add `uvicorn` and `starlette` to the `dependencies` list:

```toml
dependencies = [
  "anyio>=4.0.0",
  "claude-agent-sdk>=0.1.44",
  "pydantic>=2.8.0",
  "PyJWT[crypto]>=2.8.0",
  "rich>=13.7.0",
  "starlette>=0.44.0",
  "typer>=0.12.3",
  "uvicorn>=0.34.0",
]
```

- [ ] **Step 2: Sync dependencies**

Run: `uv sync`
Expected: resolves without conflict (both already in lock file transitively).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add uvicorn and starlette as direct dependencies"
```

---

### Task 2: Rewrite history_server.py HTTP layer to Starlette

**Files:**
- Modify: `src/code_reviewer/history_server.py:250-409` (HTTP server section)

Keep everything from line 1-248 (all scanning helpers, `_API_ROUTES`, constants) unchanged. Replace the `_make_handler` class, `HistoryHandler`, and `run_history_server` with a Starlette ASGI app.

- [ ] **Step 1: Write HTTP-level test for the Starlette app**

Add to `tests/test_history_server.py` — new tests using Starlette `TestClient`:

```python
from starlette.testclient import TestClient
from code_reviewer.history_server import create_history_app


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
    # Known static file
    resp = client.get("/")
    assert resp.status_code == 200
    assert "SPA" in resp.text
    # SPA fallback for unknown route
    resp = client.get("/some/spa/route")
    assert resp.status_code == 200
    assert "SPA" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_history_server.py -v -k "endpoint or fallback"`
Expected: FAIL — `create_history_app` does not exist yet.

- [ ] **Step 3: Implement `create_history_app` in history_server.py**

Replace everything from the `# HTTP server` comment (line 250) to the end of the file. Keep the `_API_ROUTES` list removal — routes are now defined as Starlette routes.

```python
# ---------------------------------------------------------------------------
# Starlette ASGI application
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
        """Serve index.html for any path not matched by API or static files."""
        if static_dir is None:
            return _json({"error": "not found"}, 404)
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

    if static_dir and static_dir.is_dir():
        routes.append(Mount("/", app=StaticFiles(directory=str(static_dir), html=True)))

    # SPA fallback must be last
    routes.append(Route("/{path:path}", spa_fallback))

    middleware = []
    if enable_cors:
        from starlette.middleware.cors import CORSMiddleware

        middleware.append(
            Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "OPTIONS"])
        )

    return Starlette(routes=routes, middleware=middleware)
```

Add the necessary imports at the top of the file (after existing imports):

```python
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
```

Remove the old `_API_ROUTES`, `_make_handler`, and `run_history_server` code.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_history_server.py -v`
Expected: all tests pass (both old scanning helper tests and new HTTP tests).

- [ ] **Step 5: Run linter**

Run: `uv run ruff check src/code_reviewer/history_server.py && uv run ruff format src/code_reviewer/history_server.py`

- [ ] **Step 6: Commit**

```bash
git add src/code_reviewer/history_server.py tests/test_history_server.py
git commit -m "feat: rewrite history server HTTP layer to Starlette ASGI app"
```

---

### Task 3: Add daemon lifespan integration

**Files:**
- Modify: `src/code_reviewer/daemon.py:109-146`

- [ ] **Step 1: Write test for `create_daemon_app`**

Add `tests/test_daemon_app.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from code_reviewer.daemon import create_daemon_app


def test_daemon_app_healthz(tmp_path: Path) -> None:
    """The daemon app serves /healthz while the daemon task is conceptually running."""
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    # Mock start_daemon so we don't need real config/preflight
    with patch("code_reviewer.daemon.start_daemon", new_callable=AsyncMock):
        app = create_daemon_app(
            config=None,  # type: ignore[arg-type]
            preflight=None,  # type: ignore[arg-type]
            store=None,  # type: ignore[arg-type]
            reviews_dir=reviews,
        )
        client = TestClient(app)
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_daemon_app.py -v`
Expected: FAIL — `create_daemon_app` does not exist.

- [ ] **Step 3: Implement `create_daemon_app` in daemon.py**

Add at the end of `daemon.py`:

```python
def create_daemon_app(
    *,
    config: AppConfig,
    preflight: PreflightResult,
    store: StateStore,
    reviews_dir: Path = Path("./reviews"),
    static_dir: Path | None = None,
    reload_config: Callable[[], AppConfig] | None = None,
) -> Starlette:
    """Create a Starlette app that runs the daemon as a background task."""
    from contextlib import asynccontextmanager

    from starlette.applications import Starlette

    from code_reviewer.history_server import create_history_app

    @asynccontextmanager
    async def lifespan(app: Starlette):
        task = asyncio.create_task(
            start_daemon(config, preflight, store, reload_config=reload_config)
        )
        yield
        # Signal the daemon to stop — start_daemon listens for SIGINT/SIGTERM
        # but in lifespan context uvicorn handles signals, so we cancel the task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    history_app = create_history_app(reviews_dir=reviews_dir, static_dir=static_dir)
    history_app.router.lifespan_context = lifespan
    return history_app
```

Add import at top: `from starlette.applications import Starlette` (only needed in the type hint).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_daemon_app.py tests/test_daemon.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/code_reviewer/daemon.py tests/test_daemon_app.py
git commit -m "feat: add create_daemon_app with lifespan-managed daemon task"
```

---

### Task 4: Add `--web-port` to `start` command and migrate `history`

**Files:**
- Modify: `src/code_reviewer/cli.py:726-806` (start_command)
- Modify: `src/code_reviewer/cli.py:1108-1167` (history_command)

- [ ] **Step 1: Add `--web-port` option to `start_command`**

Add after the existing option parameters in `start_command` (after `lightweight_review_reasoning_effort`):

```python
    web_port: Annotated[
        int | None,
        typer.Option("--web-port", help="Port for the history web UI. Enables embedded web server."),
    ] = None,
```

Then modify the try block (replacing lines 795-803):

```python
    try:
        refresh_github_token()
        preflight = run_preflight(cfg)
        if web_port is not None:
            import uvicorn

            from code_reviewer.daemon import create_daemon_app

            resolved_static = _resolve_static_dir()
            app = create_daemon_app(
                config=cfg,
                preflight=preflight,
                store=store,
                reviews_dir=Path(cfg.reviews_dir) if hasattr(cfg, "reviews_dir") else Path("./reviews"),
                static_dir=resolved_static,
                reload_config=reload_config,
            )
            info(f"Starting daemon with web UI on port {web_port}")
            uvicorn.run(app, host="0.0.0.0", port=web_port, log_level="warning")
        else:
            asyncio.run(start_daemon(cfg, preflight, store, reload_config=reload_config))
    except KeyboardInterrupt:
        info("Shutting down daemon")
    except Exception as exc:  # noqa: BLE001
        error(str(exc))
        raise typer.Exit(code=1) from exc
    finally:
        info("Released state lock")
        store.release_lock()
```

Add a helper above the command (near the history option annotations):

```python
def _resolve_static_dir() -> Path | None:
    default_static = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
    return default_static if default_static.is_dir() else None
```

- [ ] **Step 2: Migrate `history_command` to use uvicorn**

Replace the body of `history_command` (lines 1143-1166):

```python
    import logging

    import uvicorn

    from code_reviewer.history_server import create_history_app

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    resolved_static = static_dir
    if resolved_static is None:
        resolved_static = _resolve_static_dir()
    info(f"Starting history server on {host}:{port}")
    info(f"Reviews directory: {reviews_dir.resolve()}")
    if resolved_static:
        info(f"Serving frontend from: {resolved_static.resolve()}")
    elif not dev:
        warn("No static directory found. Run 'npm run build' in web/ to enable the frontend.")
    app = create_history_app(
        reviews_dir=reviews_dir,
        static_dir=resolved_static,
        enable_cors=dev,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
```

Remove the old `run_history_server` import from the top of cli.py.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: all tests pass.

- [ ] **Step 4: Run linter and formatter**

Run: `uv run ruff check src/code_reviewer/cli.py src/code_reviewer/daemon.py && uv run ruff format .`

- [ ] **Step 5: Commit**

```bash
git add src/code_reviewer/cli.py
git commit -m "feat: add --web-port to start command, migrate history to uvicorn"
```

---

### Task 5: Clean up and verify

- [ ] **Step 1: Remove dead code**

Check that the old `run_history_server` function, `_make_handler`, `_API_ROUTES` list, and `HistoryHandler` class are fully removed from `history_server.py`. Remove any unused imports (`http.server`, `http.HTTPStatus`, `mimetypes`).

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 3: Run linter**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean.

- [ ] **Step 4: Manual smoke test**

Run: `uv run code-reviewer history --port 8081 --reviews-dir ./reviews`
Then in another terminal: `curl http://localhost:8081/healthz`
Expected: `{"status":"ok"}`

- [ ] **Step 5: Commit if any cleanup was needed**

```bash
git add -u
git commit -m "refactor: remove dead stdlib HTTP server code from history_server"
```
