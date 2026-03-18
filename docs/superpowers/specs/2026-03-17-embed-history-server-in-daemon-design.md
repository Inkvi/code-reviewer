# Embed History Web UI in Daemon Process

## Goal

Allow the code-reviewer K8s pod to serve the PR review history web UI alongside the daemon, accessible via `kubectl port-forward`. Follows the same pattern as the autopilot service: one process, one container, uvicorn + async daemon loop via Starlette lifespan.

## Current State

- `start` command runs `asyncio.run(start_daemon(...))` — pure polling loop, no HTTP server.
- `history` command runs a standalone stdlib `http.server` serving JSON API + React static files.
- K8s deployment has one container running `code-reviewer start`. No port exposed, no Service.
- `reviews/` volume is `emptyDir` — lost on pod restart.

## Design

### Approach

When `--web-port` is passed to `code-reviewer start`, run uvicorn as the main event loop. The daemon polling loop runs as a background `asyncio.create_task` inside a Starlette lifespan context manager. When `--web-port` is omitted, behavior is unchanged.

### File Changes

#### `pyproject.toml`

Add `uvicorn` and `starlette` to `dependencies`. Both are already in the lock file (transitive deps of `claude-agent-sdk`), so this pins them as direct dependencies.

#### `history_server.py` — Starlette ASGI app

Replace `http.server.HTTPServer` / `BaseHTTPRequestHandler` with a Starlette ASGI application.

- All filesystem scanning helpers (`list_repos`, `list_prs`, `get_pr_detail`, `get_pr_history`, `get_version_detail`, `get_stage_content`) are unchanged.
- New `create_history_app(reviews_dir, static_dir, enable_cors)` factory returns a Starlette app.
- Routes map 1:1 to existing `_API_ROUTES`:
  - `GET /healthz`
  - `GET /api/repos`
  - `GET /api/repos/{org}/{repo}/prs`
  - `GET /api/repos/{org}/{repo}/prs/{number}`
  - `GET /api/repos/{org}/{repo}/prs/{number}/history`
  - `GET /api/repos/{org}/{repo}/prs/{number}/history/{version}`
  - `GET /api/repos/{org}/{repo}/prs/{number}/stages/{stage}`
- Static file serving with SPA fallback (serve `index.html` for unmatched paths).
- CORS middleware when `enable_cors=True`.

#### `daemon.py` — lifespan integration

New function `create_daemon_app(...)` that:

1. Accepts the same parameters as `start_daemon` plus `reviews_dir`, `static_dir`.
2. Builds a Starlette lifespan that `create_task`s the daemon loop on startup and cancels it on shutdown.
3. Returns a Starlette app (from `create_history_app`) wrapped with the daemon lifespan.

The existing `start_daemon` function remains for non-web usage (`--web-port` omitted).

#### `cli.py` — `--web-port` option on `start`

- New optional `--web-port` parameter (default: `None`).
- When set: call `uvicorn.run()` with the app from `create_daemon_app()`, binding to `0.0.0.0:{web_port}`.
- When `None`: current `asyncio.run(start_daemon(...))` path.

#### `cli.py` — `history` command migration

The standalone `history` command switches to `uvicorn.run(create_history_app(...))`. Same behavior, just using the new Starlette app instead of stdlib. Keeps working for local use without the daemon.

### What Doesn't Change

- Filesystem scanning helpers in `history_server.py`.
- `daemon.py` core logic (`run_cycle`, `start_daemon` polling loop).
- Webhook server (`webhook.py`) — separate command, separate concern.
- Tests for scanning helpers and daemon behavior.

### K8s Changes (infra repo, separate PR)

- Add `--web-port 8081` to the container command in `code-reviewer.yaml`.
- Add `containerPort: 8081` and a named port.
- Add liveness/readiness probes on `/healthz:8081`.
- Add a `Service` resource targeting the web port.
- Move `reviews` volume from `emptyDir` to the existing PVC (or a dedicated one).

## Testing

- Existing `history_server.py` unit tests updated to use Starlette test client instead of raw HTTP.
- Verify `start` without `--web-port` still works as before (no regression).
- Verify `start --web-port 8081` serves `/healthz` and `/api/repos` while the daemon polls.
- Verify standalone `history` command still works.
