import pytest

from code_reviewer import backend_usage
from code_reviewer.reviewers._circuit_breaker import _circuits


@pytest.fixture(autouse=True)
def _clear_circuit_breaker_state():
    """Ensure circuit breaker state doesn't leak between tests."""
    _circuits.clear()
    yield
    _circuits.clear()


@pytest.fixture(autouse=True)
def _isolate_backend_usage_dirs(monkeypatch, tmp_path):
    """Keep usage checks from scanning the real ~/.codex and Claude app folders."""
    monkeypatch.setattr(backend_usage, "_default_codex_home", lambda: tmp_path / ".codex")
    monkeypatch.setattr(
        backend_usage, "_default_claude_support_dir", lambda: tmp_path / "claude-support"
    )
