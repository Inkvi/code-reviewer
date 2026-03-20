from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

_CONSECUTIVE_FAILURE_THRESHOLD = 3
_GENERIC_COOLDOWN = timedelta(minutes=5)

# Pattern: "reset after 10h24m56s" or "reset after 5m30s" or "reset after 45s"
_QUOTA_RESET_RE = re.compile(r"reset after (?:(\d+)h)?(?:(\d+)m)?(\d+)s")


def _parse_cooldown(error_text: str) -> timedelta | None:
    """Extract a cooldown duration from known quota error patterns."""
    m = _QUOTA_RESET_RE.search(error_text)
    if not m:
        return None
    hours = int(m.group(1)) if m.group(1) else 0
    minutes = int(m.group(2)) if m.group(2) else 0
    seconds = int(m.group(3)) if m.group(3) else 0
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


@dataclass
class CircuitState:
    open_until: datetime
    reason: str
    consecutive_failures: int = 0


_circuits: dict[tuple[str, str | None], CircuitState] = {}


def _now() -> datetime:
    return datetime.now(UTC)


def _format_remaining(open_until: datetime) -> str:
    remaining = open_until - _now()
    if remaining.total_seconds() <= 0:
        return "0s"
    total_secs = int(remaining.total_seconds())
    hours, remainder = divmod(total_secs, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return "".join(parts)


def is_open(backend: str, model: str | None) -> tuple[bool, str | None]:
    """Check if a circuit is open (backend should be skipped)."""
    key = (backend, model)
    state = _circuits.get(key)
    if state is None:
        return False, None
    if _now() >= state.open_until:
        return False, None
    remaining = _format_remaining(state.open_until)
    return True, f"{state.reason} (resets in {remaining})"


def record_failure(backend: str, model: str | None, exc: Exception) -> None:
    """Record a backend failure; may trip the circuit."""
    key = (backend, model)
    error_text = str(exc)
    cooldown = _parse_cooldown(error_text)

    state = _circuits.get(key)
    # Reset stale count if a previously-tripped circuit's cooldown has expired
    if (
        state is not None
        and state.consecutive_failures >= _CONSECUTIVE_FAILURE_THRESHOLD
        and _now() >= state.open_until
    ):
        state.consecutive_failures = 0
    consecutive = (state.consecutive_failures if state else 0) + 1

    if cooldown is not None:
        label = f"{backend}/{model}" if model else backend
        reason = f"quota exhausted on {label}"
        _circuits[key] = CircuitState(
            open_until=_now() + cooldown,
            reason=reason,
            consecutive_failures=0,
        )
        log.warning(
            "circuit open for %s/%s: %s (cooldown %s)",
            backend,
            model or "default",
            reason,
            cooldown,
        )
        return

    if consecutive >= _CONSECUTIVE_FAILURE_THRESHOLD:
        label = f"{backend}/{model}" if model else backend
        reason = f"{consecutive} consecutive failures on {label}"
        _circuits[key] = CircuitState(
            open_until=_now() + _GENERIC_COOLDOWN,
            reason=reason,
            consecutive_failures=consecutive,
        )
        log.warning(
            "circuit open for %s/%s: %s (cooldown %s)",
            backend,
            model or "default",
            reason,
            _GENERIC_COOLDOWN,
        )
    else:
        _circuits[key] = CircuitState(
            open_until=datetime.min.replace(tzinfo=UTC),
            reason="",
            consecutive_failures=consecutive,
        )


def record_success(backend: str, model: str | None) -> None:
    """Reset consecutive failure count on success. Does not close quota-based circuits early."""
    key = (backend, model)
    state = _circuits.get(key)
    if state is None:
        return
    state.consecutive_failures = 0
