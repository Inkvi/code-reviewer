"""GitHub App webhook receiver for code-reviewer.

Receives pull_request, issue_comment, and check_suite events from a GitHub App,
validates the HMAC-SHA256 signature, and spawns review jobs via
``code-reviewer run-once --pr-url``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger(__name__)

_REVIEW_CMD_RE = re.compile(r"^\s*/review(?:\s+(force))?\s*$", re.MULTILINE)


@dataclass(slots=True)
class WebhookConfig:
    """Configuration for the webhook server (read from environment variables)."""

    webhook_secret: str = ""
    host: str = "0.0.0.0"
    port: int = 8000
    review_command: list[str] = field(default_factory=lambda: ["code-reviewer", "run-once"])
    review_args: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> WebhookConfig:
        return cls(
            webhook_secret=os.environ.get("WEBHOOK_SECRET", ""),
            host=os.environ.get("WEBHOOK_HOST", "0.0.0.0"),
            port=int(os.environ.get("WEBHOOK_PORT", "8000")),
        )


def validate_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Validate GitHub webhook HMAC-SHA256 signature."""
    if not secret:
        return True
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def parse_event(event_type: str, payload: dict[str, Any]) -> list[str]:
    """Extract the PR URLs to run for a webhook payload.

    Returns an empty list if the event should be ignored.
    """
    if event_type == "check_suite":
        return _handle_check_suite(payload)
    url = None
    if event_type == "pull_request":
        url = _handle_pull_request(payload)
    elif event_type == "issue_comment":
        url = _handle_issue_comment(payload)
    return [url] if url else []


def _handle_pull_request(payload: dict[str, Any]) -> str | None:
    action = payload.get("action", "")
    actionable = {"opened", "synchronize", "review_requested"}
    if action not in actionable:
        return None

    pr = payload.get("pull_request", {})
    if pr.get("draft"):
        return None

    url = pr.get("html_url")
    if not isinstance(url, str) or not url:
        return None
    return url


def _handle_issue_comment(payload: dict[str, Any]) -> str | None:
    action = payload.get("action", "")
    if action != "created":
        return None

    comment = payload.get("comment", {})
    body = comment.get("body", "")
    if not _REVIEW_CMD_RE.search(body):
        return None

    issue = payload.get("issue", {})
    if "pull_request" not in issue:
        return None

    pr_url = issue.get("pull_request", {}).get("html_url")
    if not isinstance(pr_url, str) or not pr_url:
        # Construct PR URL from issue URL by replacing /issues/ with /pull/.
        html_url = issue.get("html_url")
        if isinstance(html_url, str) and html_url:
            return html_url.replace("/issues/", "/pull/")
        return None
    return pr_url


def _handle_check_suite(payload: dict[str, Any]) -> list[str]:
    """When CI finishes, run again so PRs waiting on their checks can be approved.

    GitHub leaves pull_requests empty for PRs from forks, so those are not rechecked.
    """
    if payload.get("action") != "completed":
        return []
    repo_url = payload.get("repository", {}).get("html_url")
    if not isinstance(repo_url, str) or not repo_url:
        return []
    pull_requests = payload.get("check_suite", {}).get("pull_requests") or []
    numbers = [pr.get("number") for pr in pull_requests]
    return [f"{repo_url}/pull/{number}" for number in numbers if isinstance(number, int)]


# Each run takes the global state lock, so runs started at the same time would fail.
_RUN_ONE_AT_A_TIME = threading.Lock()


def _run_review(cmd: list[str], pr_url: str) -> None:
    """Run one review subprocess after any earlier one finishes, and log the result."""
    with _RUN_ONE_AT_A_TIME:
        logger.info("Spawning review: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        returncode = proc.wait()
    if returncode == 0:
        logger.info("Review completed successfully: %s", pr_url)
    else:
        logger.warning("Review exited with code %d: %s", returncode, pr_url)


def spawn_review(pr_url: str, config: WebhookConfig) -> threading.Thread:
    """Run ``code-reviewer run-once --pr-url <url>`` in the background, one run at a time."""
    cmd = [*config.review_command, *config.review_args, "--pr-url", pr_url]
    thread = threading.Thread(target=_run_review, args=(cmd, pr_url), daemon=True)
    thread.start()
    return thread


def _make_handler(config: WebhookConfig) -> type[BaseHTTPRequestHandler]:
    """Create a request handler class bound to the given config."""

    class WebhookHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/webhook":
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            try:
                content_length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
                return
            if content_length <= 0 or content_length > 10 * 1024 * 1024:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return

            body = self.rfile.read(content_length)

            signature = self.headers.get("X-Hub-Signature-256", "")
            if not validate_signature(body, signature, config.webhook_secret):
                self.send_error(HTTPStatus.UNAUTHORIZED, "Invalid signature")
                return

            event_type = self.headers.get("X-GitHub-Event", "")
            if event_type == "ping":
                self._respond(HTTPStatus.OK, {"status": "pong"})
                return

            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
                return

            pr_urls = parse_event(event_type, payload)
            if not pr_urls:
                self._respond(HTTPStatus.OK, {"status": "ignored"})
                return

            for pr_url in pr_urls:
                spawn_review(pr_url, config)
            self._respond(HTTPStatus.ACCEPTED, {"status": "accepted", "pr_urls": pr_urls})

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._respond(HTTPStatus.OK, {"status": "ok"})
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def _respond(self, status: HTTPStatus, data: dict[str, Any]) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.info(format, *args)

    return WebhookHandler


def run_server(config: WebhookConfig) -> None:
    """Start the webhook HTTP server (blocks forever)."""
    handler_cls = _make_handler(config)
    server = HTTPServer((config.host, config.port), handler_cls)
    logger.info("Webhook server listening on %s:%d", config.host, config.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down webhook server")
    finally:
        server.server_close()
