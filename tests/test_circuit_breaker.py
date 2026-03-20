from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from code_reviewer.reviewers._circuit_breaker import (
    _parse_cooldown,
    is_open,
    record_failure,
    record_success,
)


def test_parse_gemini_quota_error_hms() -> None:
    err = (
        "TerminalQuotaError: You have exhausted your capacity on this model. "
        "Your quota will reset after 10h24m56s."
    )
    assert _parse_cooldown(err) == timedelta(hours=10, minutes=24, seconds=56)


def test_parse_gemini_quota_error_ms() -> None:
    err = "TerminalQuotaError: Your quota will reset after 5m30s."
    assert _parse_cooldown(err) == timedelta(minutes=5, seconds=30)


def test_parse_gemini_quota_error_s_only() -> None:
    err = "TerminalQuotaError: Your quota will reset after 45s."
    assert _parse_cooldown(err) == timedelta(seconds=45)


def test_parse_gemini_quota_error_hm_no_seconds() -> None:
    err = "TerminalQuotaError: Your quota will reset after 2h15m0s."
    assert _parse_cooldown(err) == timedelta(hours=2, minutes=15)


def test_parse_unrecognized_error() -> None:
    assert _parse_cooldown("RuntimeError: something broke") is None


def test_parse_empty_string() -> None:
    assert _parse_cooldown("") is None


# --- State management tests ---


def test_is_open_returns_false_when_no_state() -> None:
    opened, reason = is_open("gemini", "gemini-2.5-pro")
    assert opened is False
    assert reason is None


def test_record_failure_quota_error_trips_circuit() -> None:
    err = RuntimeError(
        "gemini exited with status 1: TerminalQuotaError: "
        "You have exhausted your capacity on this model. "
        "Your quota will reset after 1h0m0s."
    )
    record_failure("gemini", "gemini-2.5-pro", err)
    opened, reason = is_open("gemini", "gemini-2.5-pro")
    assert opened is True
    assert "quota exhausted" in reason


def test_record_failure_generic_needs_three_to_trip() -> None:
    err = RuntimeError("something broke")
    record_failure("gemini", "gemini-2.5-pro", err)
    assert is_open("gemini", "gemini-2.5-pro")[0] is False
    record_failure("gemini", "gemini-2.5-pro", err)
    assert is_open("gemini", "gemini-2.5-pro")[0] is False
    record_failure("gemini", "gemini-2.5-pro", err)
    assert is_open("gemini", "gemini-2.5-pro")[0] is True


def test_record_success_resets_consecutive_failures() -> None:
    err = RuntimeError("broke")
    record_failure("codex", None, err)
    record_failure("codex", None, err)
    record_success("codex", None)
    record_failure("codex", None, err)
    # Only 1 failure after reset — should NOT be open
    assert is_open("codex", None)[0] is False


def test_different_models_independent() -> None:
    err = RuntimeError("TerminalQuotaError: Your quota will reset after 5m0s.")
    record_failure("gemini", "gemini-2.5-pro", err)
    assert is_open("gemini", "gemini-2.5-pro")[0] is True
    assert is_open("gemini", "gemini-2.5-flash")[0] is False


def test_circuit_closes_after_expiry() -> None:
    err = RuntimeError("TerminalQuotaError: Your quota will reset after 1h0m0s.")
    record_failure("gemini", "gemini-2.5-pro", err)
    assert is_open("gemini", "gemini-2.5-pro")[0] is True
    future = datetime.now(UTC) + timedelta(hours=1, seconds=1)
    with patch("code_reviewer.reviewers._circuit_breaker._now", return_value=future):
        assert is_open("gemini", "gemini-2.5-pro")[0] is False


def test_stale_consecutive_count_resets_after_cooldown_expiry() -> None:
    """After a generic-failure cooldown expires, one new failure should NOT re-trip."""
    err = RuntimeError("something broke")
    # Trip with 3 consecutive failures
    record_failure("codex", None, err)
    record_failure("codex", None, err)
    record_failure("codex", None, err)
    assert is_open("codex", None)[0] is True
    # Simulate cooldown expiry (5 min + 1s)
    future = datetime.now(UTC) + timedelta(minutes=5, seconds=1)
    with patch("code_reviewer.reviewers._circuit_breaker._now", return_value=future):
        assert is_open("codex", None)[0] is False
        # One new failure after recovery — should NOT re-trip
        record_failure("codex", None, err)
        assert is_open("codex", None)[0] is False
