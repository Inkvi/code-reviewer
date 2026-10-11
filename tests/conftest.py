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
    # Without an explicit loader, the account lookup would run the real `claude auth status`.
    load_account_type = backend_usage._load_claude_account_type
    monkeypatch.setattr(
        backend_usage,
        "_load_claude_account_type",
        lambda loader=None: load_account_type(loader) if loader else None,
    )
