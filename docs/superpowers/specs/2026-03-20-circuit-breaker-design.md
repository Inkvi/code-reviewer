# Circuit Breaker for Model Backends

## Problem

When a model backend hits quota limits (e.g. Gemini's `TerminalQuotaError`), every subsequent call to that backend wastes time failing before falling back. The error message often contains a reset duration (e.g. "Your quota will reset after 10h24m56s") that we can use to avoid retrying until the quota resets.

Additionally, backends that fail repeatedly with generic errors should be temporarily disabled to avoid wasting cycles.

## Design

### Circuit Breaker Module

New file: `src/code_reviewer/reviewers/_circuit_breaker.py`

#### State

```python
@dataclass
class CircuitState:
    open_until: datetime          # when the circuit closes again
    reason: str                   # human-readable reason for logging
    consecutive_failures: int     # tracks generic failure streaks
```

Global in-memory state:

```python
_circuits: dict[tuple[str, str | None], CircuitState] = {}
```

Key is `(backend, model)` where model can be `None` for backend-wide trips.

State is in-memory only — resets on process restart. The daemon is long-running, so this covers the typical case. One wasted attempt after restart is acceptable.

#### Public API

- `is_open(backend: str, model: str | None) -> tuple[bool, str | None]`
  Returns `(True, reason)` if circuit is open, `(False, None)` otherwise. Checks `open_until` against current time.

- `record_failure(backend: str, model: str | None, exc: Exception) -> None`
  Parses the exception for known quota patterns. If a cooldown duration is found, trips the circuit for that duration. Otherwise increments `consecutive_failures`; after 3 consecutive failures, trips for 5 minutes.

- `record_success(backend: str, model: str | None) -> None`
  Resets `consecutive_failures` to 0. Does not close a quota-based circuit early (the quota window is real).

#### Error Parsing

`_parse_cooldown(error_text: str) -> timedelta | None`

Known patterns:
- Gemini `TerminalQuotaError`: extracts duration from `"reset after (\d+h)?(\d+m)?\d+s"` → `timedelta`
- Returns `None` for unrecognized errors

### Integration with `run_with_fallback`

Signature change:

```python
async def run_with_fallback[T](
    backends: list[str],
    runner: Callable[[str], Awaitable[T]],
    label: str,
    context: str,
    models: dict[str, str | None] | None = None,
) -> T:
```

`models` is optional — when `None`, circuit breaker is not consulted (backwards compatible).

#### Flow inside the loop

1. **Before calling runner:** check `is_open(backend, models.get(backend))`. If open, log warning (e.g. `"skipping gemini/gemini-2.5-pro (circuit open, resets in 9h12m)"`) and `continue` to next backend. This does not count as a failure.
2. **On success:** call `record_success(backend, models.get(backend))`.
3. **On failure (exception):** call `record_failure(backend, models.get(backend), exc)`, then existing fallback logic continues.

#### Edge case: all backends circuit-open

If every backend in the list is open, try the one whose circuit closes soonest rather than failing with no attempt.

### Caller Changes

`triage.py`, `lightweight.py`, and `reconcile.py` pass a `models` dict to `run_with_fallback`. They already know which model maps to which backend — it's just threading the value through.

## Testing

Tests in `tests/test_circuit_breaker.py`:

1. `_parse_cooldown` — extracts correct timedelta from Gemini quota error; returns `None` for unrecognized errors
2. `record_failure` with quota error — trips circuit with parsed duration
3. `record_failure` generic errors — trips after 3 consecutive failures with 5min cooldown
4. `record_success` — resets consecutive failure counter
5. `is_open` — returns `(True, reason)` when tripped; `(False, None)` when expired or not tripped
6. `run_with_fallback` integration — skips circuit-open backends, logs warning, tries next
7. All backends open — falls back to soonest-closing backend
8. Different models same backend — tripping `("gemini", "pro")` doesn't affect `("gemini", "flash")`
9. `PromptOverrideError` still bypasses — circuit breaker doesn't interfere
