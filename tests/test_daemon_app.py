from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from code_reviewer.daemon import create_daemon_app


def test_daemon_app_healthz(tmp_path: Path) -> None:
    """The daemon app serves /healthz while the daemon task is conceptually running."""
    reviews = tmp_path / "reviews"
    reviews.mkdir()
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
