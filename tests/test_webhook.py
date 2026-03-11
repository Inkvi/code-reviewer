from __future__ import annotations

import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

from code_reviewer.webhook import (
    WebhookConfig,
    _make_handler,
    parse_event,
    spawn_review,
    validate_signature,
)

# ---------------------------------------------------------------------------
# validate_signature
# ---------------------------------------------------------------------------


def test_validate_signature_valid():
    secret = "test-secret"
    payload = b'{"action": "opened"}'
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    sig = f"sha256={digest}"
    assert validate_signature(payload, sig, secret) is True


def test_validate_signature_invalid():
    assert validate_signature(b"body", "sha256=bad", "secret") is False


def test_validate_signature_empty_secret_skips():
    assert validate_signature(b"body", "", "") is True


def test_validate_signature_missing_prefix():
    assert validate_signature(b"body", "md5=abc", "secret") is False


# ---------------------------------------------------------------------------
# parse_event — pull_request
# ---------------------------------------------------------------------------


def _pr_payload(
    action: str = "opened", draft: bool = False, url: str = "https://github.com/o/r/pull/1"
):
    return {
        "action": action,
        "pull_request": {
            "html_url": url,
            "draft": draft,
        },
    }


def test_parse_pr_opened():
    assert parse_event("pull_request", _pr_payload("opened")) == "https://github.com/o/r/pull/1"


def test_parse_pr_synchronize():
    assert parse_event("pull_request", _pr_payload("synchronize")) is not None


def test_parse_pr_review_requested():
    assert parse_event("pull_request", _pr_payload("review_requested")) is not None


def test_parse_pr_closed_ignored():
    assert parse_event("pull_request", _pr_payload("closed")) is None


def test_parse_pr_draft_ignored():
    assert parse_event("pull_request", _pr_payload("opened", draft=True)) is None


def test_parse_pr_missing_url():
    payload = {"action": "opened", "pull_request": {"draft": False}}
    assert parse_event("pull_request", payload) is None


# ---------------------------------------------------------------------------
# parse_event — issue_comment (/review)
# ---------------------------------------------------------------------------


def _comment_payload(body: str = "/review", is_pr: bool = True):
    payload: dict = {
        "action": "created",
        "comment": {"body": body},
        "issue": {
            "html_url": "https://github.com/o/r/issues/1",
        },
    }
    if is_pr:
        payload["issue"]["pull_request"] = {
            "html_url": "https://github.com/o/r/pull/1",
        }
    return payload


def test_parse_comment_review():
    result = parse_event("issue_comment", _comment_payload("/review"))
    assert result == "https://github.com/o/r/pull/1"


def test_parse_comment_review_force():
    result = parse_event("issue_comment", _comment_payload("/review force"))
    assert result is not None


def test_parse_comment_not_review():
    assert parse_event("issue_comment", _comment_payload("nice work!")) is None


def test_parse_comment_not_pr():
    assert parse_event("issue_comment", _comment_payload("/review", is_pr=False)) is None


def test_parse_comment_edited_ignored():
    payload = _comment_payload("/review")
    payload["action"] = "edited"
    assert parse_event("issue_comment", payload) is None


def test_parse_comment_fallback_to_issue_url():
    """When pull_request.html_url is missing, fall back to issue.html_url converted to /pull/."""
    payload = {
        "action": "created",
        "comment": {"body": "/review"},
        "issue": {
            "html_url": "https://github.com/o/r/issues/5",
            "pull_request": {},
        },
    }
    result = parse_event("issue_comment", payload)
    assert result == "https://github.com/o/r/pull/5"


# ---------------------------------------------------------------------------
# parse_event — unknown event
# ---------------------------------------------------------------------------


def test_parse_unknown_event():
    assert parse_event("push", {"ref": "refs/heads/main"}) is None


# ---------------------------------------------------------------------------
# spawn_review
# ---------------------------------------------------------------------------


def test_spawn_review(monkeypatch):
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)

        class FakeProc:
            pid = 42

            def wait(self):
                return 0

        return FakeProc()

    monkeypatch.setattr("code_reviewer.webhook.subprocess.Popen", fake_popen)

    config = WebhookConfig(review_command=["code-reviewer", "run-once"])
    spawn_review("https://github.com/o/r/pull/1", config)
    assert calls == [["code-reviewer", "run-once", "--pr-url", "https://github.com/o/r/pull/1"]]


def test_spawn_review_with_extra_args(monkeypatch):
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)

        class FakeProc:
            pid = 42

            def wait(self):
                return 0

        return FakeProc()

    monkeypatch.setattr("code_reviewer.webhook.subprocess.Popen", fake_popen)

    config = WebhookConfig(
        review_command=["code-reviewer", "run-once"],
        review_args=["--auto-post-review"],
    )
    spawn_review("https://github.com/o/r/pull/1", config)
    assert calls[0] == [
        "code-reviewer",
        "run-once",
        "--auto-post-review",
        "--pr-url",
        "https://github.com/o/r/pull/1",
    ]


# ---------------------------------------------------------------------------
# WebhookConfig.from_env
# ---------------------------------------------------------------------------


def test_webhook_config_from_env(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("WEBHOOK_HOST", "127.0.0.1")
    monkeypatch.setenv("WEBHOOK_PORT", "9090")
    cfg = WebhookConfig.from_env()
    assert cfg.webhook_secret == "s3cret"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 9090


def test_webhook_config_defaults(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("WEBHOOK_HOST", raising=False)
    monkeypatch.delenv("WEBHOOK_PORT", raising=False)
    cfg = WebhookConfig.from_env()
    assert cfg.webhook_secret == ""
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 8000


# ---------------------------------------------------------------------------
# HTTP server integration tests
# ---------------------------------------------------------------------------


def _start_test_server(config: WebhookConfig):
    """Start a webhook server in a daemon thread, return (server, base_url)."""
    from http.server import HTTPServer

    handler_cls = _make_handler(config)
    server = HTTPServer((config.host, config.port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{config.host}:{config.port}"


def _post(base_url: str, path: str, body: bytes, headers: dict | None = None):
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers=headers or {},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def test_server_healthz():
    config = WebhookConfig(host="127.0.0.1", port=0)
    from http.server import HTTPServer

    handler_cls = _make_handler(config)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        url = f"http://127.0.0.1:{port}/healthz"
        resp = urllib.request.urlopen(url)
        data = json.loads(resp.read())
        assert data == {"status": "ok"}
    finally:
        server.shutdown()


def test_server_ping():
    config = WebhookConfig(host="127.0.0.1", port=0)
    from http.server import HTTPServer

    handler_cls = _make_handler(config)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/webhook",
            data=b"{}",
            headers={
                "X-GitHub-Event": "ping",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        assert data == {"status": "pong"}
    finally:
        server.shutdown()


def test_server_rejects_bad_signature():
    config = WebhookConfig(host="127.0.0.1", port=0, webhook_secret="secret")
    from http.server import HTTPServer

    handler_cls = _make_handler(config)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/webhook",
            data=b'{"action":"opened"}',
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=wrong",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            raise AssertionError("Expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 401
    finally:
        server.shutdown()


def test_server_accepts_valid_pr_event():
    config = WebhookConfig(host="127.0.0.1", port=0)
    from http.server import HTTPServer

    handler_cls = _make_handler(config)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    spawned = []

    try:
        body = json.dumps(_pr_payload("opened")).encode()
        with patch("code_reviewer.webhook.spawn_review") as mock_spawn:
            mock_spawn.side_effect = lambda url, cfg: spawned.append(url)
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/webhook",
                data=body,
                headers={
                    "X-GitHub-Event": "pull_request",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req)
            data = json.loads(resp.read())
            assert resp.status == 202
            assert data["status"] == "accepted"
            assert spawned == ["https://github.com/o/r/pull/1"]
    finally:
        server.shutdown()


def test_server_ignores_non_actionable():
    config = WebhookConfig(host="127.0.0.1", port=0)
    from http.server import HTTPServer

    handler_cls = _make_handler(config)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        body = json.dumps(_pr_payload("closed")).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/webhook",
            data=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        assert data == {"status": "ignored"}
    finally:
        server.shutdown()


def test_server_rejects_non_numeric_content_length():
    config = WebhookConfig(host="127.0.0.1", port=0)
    from http.server import HTTPServer

    handler_cls = _make_handler(config)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/webhook",
            data=b'{"action":"opened"}',
            headers={
                "X-GitHub-Event": "pull_request",
                "Content-Length": "not-a-number",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            raise AssertionError("Expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        server.shutdown()
