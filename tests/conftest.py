import pytest

from code_reviewer.reviewers._circuit_breaker import _circuits


@pytest.fixture(autouse=True)
def _clear_circuit_breaker_state():
    """Ensure circuit breaker state doesn't leak between tests."""
    _circuits.clear()
    yield
    _circuits.clear()
