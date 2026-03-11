"""GitHub App webhook receiver for code-reviewer.

Receives pull_request and issue_comment events from a GitHub App,
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


def parse_event(event_type: str, payload: dict[str, Any]) -> str | None:
    """Extract a PR URL from a webhook payload if the event is actionable.

    Returns the PR URL string, or None if the event should be ignored.
    """
    if event_type == "pull_request":
        return _handle_pull_request(payload)
    if event_type == "issue_comment":
        return _handle_issue_comment(payload)
    return None


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


def _reap_process(proc: subprocess.Popen[bytes], pr_url: str) -> None:
    """Wait for a review subprocess to finish and log the result."""
    returncode = proc.wait()
    if returncode == 0:
        logger.info("Review completed successfully: %s", pr_url)
    else:
        logger.warning("Review exited with code %d: %s", returncode, pr_url)


def spawn_review(pr_url: str, config: WebhookConfig) -> None:
    """Spawn ``code-reviewer run-once --pr-url <url>`` as a background process."""
    cmd = [*config.review_command, *config.review_args, "--pr-url", pr_url]
    logger.info("Spawning review: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    threading.Thread(target=_reap_process, args=(proc, pr_url), daemon=True).start()


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

            pr_url = parse_event(event_type, payload)
            if pr_url is None:
                self._respond(HTTPStatus.OK, {"status": "ignored"})
                return

            spawn_review(pr_url, config)
            self._respond(HTTPStatus.ACCEPTED, {"status": "accepted", "pr_url": pr_url})

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
