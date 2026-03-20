# Circuit Breaker for Model Backends — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent wasted calls to quota-exhausted or repeatedly-failing model backends by tracking failures per (backend, model) and skipping open circuits until cooldown expires.

**Architecture:** A standalone `_circuit_breaker.py` module holds in-memory state keyed by `(backend, model)`. It parses known quota error patterns (e.g. Gemini `TerminalQuotaError`) for precise cooldowns, and trips after 3 consecutive generic failures with a 5-minute default. `run_with_fallback` checks/updates the breaker before/after each backend call. Full review launcher in `processor.py` also checks before starting reviewer tasks.

**Tech Stack:** Python 3.12+, dataclasses, re, datetime

**Spec:** `docs/superpowers/specs/2026-03-20-circuit-breaker-design.md`

---

## File Structure

- **Create:** `src/code_reviewer/reviewers/_circuit_breaker.py` — circuit breaker state, error parsing, public API
- **Modify:** `src/code_reviewer/reviewers/_fallback.py:11-38` — check/update circuit breaker in `run_with_fallback`
- **Modify:** `src/code_reviewer/reviewers/triage.py:140` — pass `models` dict to `run_with_fallback`
- **Modify:** `src/code_reviewer/reviewers/lightweight.py:89` — pass `models` dict to `run_with_fallback`
- **Modify:** `src/code_reviewer/reviewers/reconcile.py:90` — pass `models` dict to `run_with_fallback`
- **Modify:** `src/code_reviewer/processor.py:524-558` — check circuit breaker before launching full review tasks, record results after
- **Create:** `tests/test_circuit_breaker.py` — unit tests for breaker module
- **Modify:** `tests/test_fallback.py` — integration tests for fallback + breaker

---

### Task 1: `_circuit_breaker.py` — Error parsing

**Files:**
- Create: `src/code_reviewer/reviewers/_circuit_breaker.py`
- Create: `tests/test_circuit_breaker.py`

- [ ] **Step 1: Write failing tests for `_parse_cooldown`**

```python
# tests/test_circuit_breaker.py
from datetime import timedelta

from code_reviewer.reviewers._circuit_breaker import _parse_cooldown


def test_parse_gemini_quota_error_hms() -> None:
    err = "TerminalQuotaError: You have exhausted your capacity on this model. Your quota will reset after 10h24m56s."
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_circuit_breaker.py -v`
Expected: ImportError — module doesn't exist yet

- [ ] **Step 3: Implement `_parse_cooldown`**

```python
# src/code_reviewer/reviewers/_circuit_breaker.py
from __future__ import annotations

import re
from datetime import timedelta

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_circuit_breaker.py -v`
Expected: All 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/code_reviewer/reviewers/_circuit_breaker.py tests/test_circuit_breaker.py
git commit -m "feat: add _parse_cooldown for extracting quota reset durations"
```

---

### Task 2: `_circuit_breaker.py` — Core state management

**Files:**
- Modify: `src/code_reviewer/reviewers/_circuit_breaker.py`
- Modify: `tests/test_circuit_breaker.py`

- [ ] **Step 1: Write failing tests for `is_open`, `record_failure`, `record_success`**

```python
# append to tests/test_circuit_breaker.py
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from code_reviewer.reviewers._circuit_breaker import (
    _circuits,
    is_open,
    record_failure,
    record_success,
)


def _clear_circuits():
    _circuits.clear()


def test_is_open_returns_false_when_no_state() -> None:
    _clear_circuits()
    opened, reason = is_open("gemini", "gemini-2.5-pro")
    assert opened is False
    assert reason is None


def test_record_failure_quota_error_trips_circuit() -> None:
    _clear_circuits()
    err = RuntimeError(
        "gemini exited with status 1: TerminalQuotaError: "
        "You have exhausted your capacity on this model. "
        "Your quota will reset after 1h0m0s."
    )
    record_failure("gemini", "gemini-2.5-pro", err)
    opened, reason = is_open("gemini", "gemini-2.5-pro")
    assert opened is True
    assert "1h" in reason


def test_record_failure_generic_needs_three_to_trip() -> None:
    _clear_circuits()
    err = RuntimeError("something broke")
    record_failure("gemini", "gemini-2.5-pro", err)
    assert is_open("gemini", "gemini-2.5-pro")[0] is False
    record_failure("gemini", "gemini-2.5-pro", err)
    assert is_open("gemini", "gemini-2.5-pro")[0] is False
    record_failure("gemini", "gemini-2.5-pro", err)
    assert is_open("gemini", "gemini-2.5-pro")[0] is True


def test_record_success_resets_consecutive_failures() -> None:
    _clear_circuits()
    err = RuntimeError("broke")
    record_failure("codex", None, err)
    record_failure("codex", None, err)
    record_success("codex", None)
    record_failure("codex", None, err)
    # Only 1 failure after reset — should NOT be open
    assert is_open("codex", None)[0] is False


def test_different_models_independent() -> None:
    _clear_circuits()
    err = RuntimeError(
        "TerminalQuotaError: Your quota will reset after 5m0s."
    )
    record_failure("gemini", "gemini-2.5-pro", err)
    assert is_open("gemini", "gemini-2.5-pro")[0] is True
    assert is_open("gemini", "gemini-2.5-flash", )[0] is False


def test_circuit_closes_after_expiry() -> None:
    _clear_circuits()
    err = RuntimeError(
        "TerminalQuotaError: Your quota will reset after 1h0m0s."
    )
    record_failure("gemini", "gemini-2.5-pro", err)
    assert is_open("gemini", "gemini-2.5-pro")[0] is True
    # Simulate time passing
    future = datetime.now(UTC) + timedelta(hours=1, seconds=1)
    with patch("code_reviewer.reviewers._circuit_breaker._now", return_value=future):
        assert is_open("gemini", "gemini-2.5-pro")[0] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_circuit_breaker.py -v -k "not test_parse"`
Expected: ImportError — `is_open`, `record_failure`, etc. don't exist yet

- [ ] **Step 3: Implement core state management**

Add to `src/code_reviewer/reviewers/_circuit_breaker.py`:

```python
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

log = logging.getLogger(__name__)

_CONSECUTIVE_FAILURE_THRESHOLD = 3
_GENERIC_COOLDOWN = timedelta(minutes=5)


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
            backend, model or "default", reason, cooldown,
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
            backend, model or "default", reason, _GENERIC_COOLDOWN,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_circuit_breaker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/code_reviewer/reviewers/_circuit_breaker.py tests/test_circuit_breaker.py
git commit -m "feat: add circuit breaker state management (is_open, record_failure, record_success)"
```

---

### Task 3: Integrate circuit breaker into `run_with_fallback`

**Files:**
- Modify: `src/code_reviewer/reviewers/_fallback.py:11-38`
- Modify: `tests/test_fallback.py`

- [ ] **Step 1: Write failing tests for circuit breaker integration in fallback**

```python
# append to tests/test_fallback.py
from code_reviewer.reviewers._circuit_breaker import _circuits, record_failure


def _clear_circuits():
    _circuits.clear()


def test_skips_circuit_open_backend() -> None:
    _clear_circuits()
    # Trip gemini with a quota error
    err = RuntimeError("TerminalQuotaError: Your quota will reset after 1h0m0s.")
    record_failure("gemini", "gemini-2.5-pro", err)

    call_log: list[str] = []

    async def runner(backend: str) -> str:
        call_log.append(backend)
        return f"ok-{backend}"

    models = {"gemini": "gemini-2.5-pro", "claude": None}
    result = asyncio.run(
        run_with_fallback(["gemini", "claude"], runner, "test", "ctx", models=models)
    )
    assert result == "ok-claude"
    assert call_log == ["claude"]  # gemini was skipped


def test_all_open_tries_soonest_closing() -> None:
    _clear_circuits()
    from datetime import UTC, datetime, timedelta
    from code_reviewer.reviewers._circuit_breaker import CircuitState

    # gemini closes in 1 hour, claude closes in 2 hours
    _circuits[("gemini", None)] = CircuitState(
        open_until=datetime.now(UTC) + timedelta(hours=1),
        reason="quota",
    )
    _circuits[("claude", None)] = CircuitState(
        open_until=datetime.now(UTC) + timedelta(hours=2),
        reason="quota",
    )

    call_log: list[str] = []

    async def runner(backend: str) -> str:
        call_log.append(backend)
        return f"ok-{backend}"

    models = {"gemini": None, "claude": None}
    result = asyncio.run(
        run_with_fallback(["claude", "gemini"], runner, "test", "ctx", models=models)
    )
    assert result == "ok-gemini"
    assert call_log == ["gemini"]  # tried soonest-closing


def test_fallback_records_failure_and_success() -> None:
    _clear_circuits()
    call_log: list[str] = []

    async def runner(backend: str) -> str:
        call_log.append(backend)
        if backend == "gemini":
            raise RuntimeError("broke")
        return f"ok-{backend}"

    models = {"gemini": "gemini-2.5-pro", "claude": None}
    result = asyncio.run(
        run_with_fallback(["gemini", "claude"], runner, "test", "ctx", models=models)
    )
    assert result == "ok-claude"
    # gemini should have 1 consecutive failure recorded
    state = _circuits.get(("gemini", "gemini-2.5-pro"))
    assert state is not None
    assert state.consecutive_failures == 1


def test_models_none_skips_circuit_breaker() -> None:
    """When models is None, circuit breaker is not consulted (backwards compatible)."""
    _clear_circuits()
    err = RuntimeError("TerminalQuotaError: Your quota will reset after 1h0m0s.")
    record_failure("gemini", None, err)

    call_log: list[str] = []

    async def runner(backend: str) -> str:
        call_log.append(backend)
        return f"ok-{backend}"

    # models=None means no circuit breaker
    result = asyncio.run(
        run_with_fallback(["gemini", "claude"], runner, "test", "ctx")
    )
    assert result == "ok-gemini"
    assert call_log == ["gemini"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_fallback.py -v -k "test_skips or test_all_open or test_fallback_records or test_models_none"`
Expected: FAIL — `run_with_fallback` doesn't accept `models` kwarg yet

- [ ] **Step 3: Update `run_with_fallback` to use circuit breaker**

Replace `src/code_reviewer/reviewers/_fallback.py` with:

```python
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from code_reviewer.prompts import PromptOverrideError
from code_reviewer.reviewers._circuit_breaker import is_open, record_failure, record_success

log = logging.getLogger(__name__)


async def run_with_fallback[T](
    backends: list[str],
    runner: Callable[[str], Awaitable[T]],
    label: str,
    context: str,
    *,
    models: dict[str, str | None] | None = None,
) -> T:
    """Try *runner* for each backend in order; re-raise last exception if all fail."""
    last_exc: Exception | None = None
    skipped: list[str] = []

    for i, backend in enumerate(backends):
        # Check circuit breaker if models mapping is provided
        if models is not None:
            model = models.get(backend)
            opened, reason = is_open(backend, model)
            if opened:
                log.warning("skipping %s/%s (circuit open: %s) %s", backend, model or "default", reason, context)
                skipped.append(backend)
                continue

        try:
            result = await runner(backend)
            if models is not None:
                record_success(backend, models.get(backend))
            return result
        except PromptOverrideError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if models is not None:
                record_failure(backend, models.get(backend), exc)
            remaining = backends[i + 1 :]
            if remaining:
                log.warning(
                    "%s failed on %s, trying %s: %s %s",
                    label,
                    backend,
                    remaining[0],
                    exc,
                    context,
                )
            else:
                log.warning("%s failed on %s (last backend): %s %s", label, backend, exc, context)

    # All backends were either skipped or failed.
    # If all were skipped due to circuit breaker, try the one closing soonest.
    if skipped and last_exc is None:
        from code_reviewer.reviewers._circuit_breaker import _circuits, _now

        soonest = min(
            skipped,
            key=lambda b: _circuits.get((b, models.get(b) if models else None), None).open_until
            if _circuits.get((b, models.get(b) if models else None))
            else _now(),
        )
        log.warning(
            "all backends circuit-open, trying soonest-closing: %s %s",
            soonest,
            context,
        )
        return await runner(soonest)

    raise last_exc  # type: ignore[misc]
```

- [ ] **Step 4: Run all fallback tests**

Run: `uv run pytest tests/test_fallback.py -v`
Expected: All PASS (old + new tests)

- [ ] **Step 5: Commit**

```bash
git add src/code_reviewer/reviewers/_fallback.py tests/test_fallback.py
git commit -m "feat: integrate circuit breaker into run_with_fallback"
```

---

### Task 4: Thread `models` dict through callers

**Files:**
- Modify: `src/code_reviewer/reviewers/triage.py:94-140`
- Modify: `src/code_reviewer/reviewers/lightweight.py:37-89`
- Modify: `src/code_reviewer/reviewers/reconcile.py:28-90`

- [ ] **Step 1: Update `triage.py` to pass `models`**

In `src/code_reviewer/reviewers/triage.py`, change the `run_with_fallback` call at line 140.

Before:
```python
        text = await run_with_fallback(backends, _try, "triage", pr.url)
```

After:
```python
        models_map = {b: (model if b == backends[0] else None) for b in backends}
        text = await run_with_fallback(backends, _try, "triage", pr.url, models=models_map)
```

- [ ] **Step 2: Update `lightweight.py` to pass `models`**

In `src/code_reviewer/reviewers/lightweight.py`, change the call at line 89.

Before:
```python
    text, usage = await run_with_fallback(backends, _try, "lightweight", pr.url)
```

After:
```python
    models_map = {b: (model if b == backends[0] else None) for b in backends}
    text, usage = await run_with_fallback(backends, _try, "lightweight", pr.url, models=models_map)
```

- [ ] **Step 3: Update `reconcile.py` to pass `models`**

In `src/code_reviewer/reviewers/reconcile.py`, change the call at line 90.

Before:
```python
    text, usage = await run_with_fallback(backends, _try, "reconcile", pr.url)
```

After:
```python
    models_map = {b: (reconciler_model if b == backends[0] else None) for b in backends}
    text, usage = await run_with_fallback(
        backends, _try, "reconcile", pr.url, models=models_map
    )
```

- [ ] **Step 4: Run existing tests to verify nothing broke**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/code_reviewer/reviewers/triage.py src/code_reviewer/reviewers/lightweight.py src/code_reviewer/reviewers/reconcile.py
git commit -m "feat: thread models dict to run_with_fallback in triage, lightweight, reconcile"
```

---

### Task 5: Integrate circuit breaker into full review launcher

**Files:**
- Modify: `src/code_reviewer/processor.py:524-618`

- [ ] **Step 1: Add circuit breaker checks before launching reviewer tasks**

In `_run_reviewers_with_monitoring`, add `is_open` checks before each `pending_tasks[...] =` line and `record_failure`/`record_success` calls after tasks complete.

Add import at top of `processor.py`:
```python
from code_reviewer.reviewers._circuit_breaker import is_open, record_failure, record_success
```

Replace the reviewer launch block (lines 528-558) with:

```python
    if "claude" in enabled_reviewer_set:
        opened, reason = is_open("claude", config.claude_model)
        if opened:
            warn(f"skipping Claude review (circuit open: {reason}) {pr.url}")
        else:
            info(
                f"starting Claude review "
                f"(backend={config.claude_backend}, model={config.claude_model or 'default'}, "
                f"effort={config.claude_reasoning_effort or 'default'}) {pr.url}"
            )
            pending_tasks["claude"] = _start_claude_review_task(config, pr, workdir)
    else:
        info(f"Claude reviewer disabled {pr.url}")

    if "codex" in enabled_reviewer_set:
        opened, reason = is_open("codex", config.codex_model)
        if opened:
            warn(f"skipping Codex review (circuit open: {reason}) {pr.url}")
        else:
            info(
                f"starting Codex review "
                f"(backend={config.codex_backend}, model={config.codex_model}, "
                f"effort={config.codex_reasoning_effort or 'default'}) {pr.url}"
            )
            pending_tasks["codex"] = _start_codex_review_task(config, pr, workdir)
    else:
        info(f"Codex reviewer disabled {pr.url}")

    if "gemini" in enabled_reviewer_set:
        opened, reason = is_open("gemini", config.gemini_model)
        if opened:
            warn(f"skipping Gemini review (circuit open: {reason}) {pr.url}")
        else:
            info(f"starting Gemini review (model={config.gemini_model or 'default'}) {pr.url}")
            pending_tasks["gemini"] = asyncio.create_task(
                run_gemini_review(
                    pr,
                    workdir,
                    config.gemini_timeout_seconds,
                    model=config.gemini_model,
                    prompt_path=config.full_review_prompt_path,
                )
            )
    else:
        info(f"Gemini reviewer disabled {pr.url}")
```

- [ ] **Step 2: Record success/failure after reviewer tasks complete**

In the task completion loop (around line 595-609), add circuit breaker recording. After the line `reviewer_outputs[reviewer_name] = output`, add:

```python
                    # Record circuit breaker state
                    _model = {
                        "claude": config.claude_model,
                        "codex": config.codex_model,
                        "gemini": config.gemini_model,
                    }.get(reviewer_name)
                    if output.status == "ok":
                        record_success(reviewer_name, _model)
                    elif output.error:
                        record_failure(reviewer_name, _model, RuntimeError(output.error))
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Lint**

Run: `uv run ruff check src/code_reviewer/reviewers/_circuit_breaker.py src/code_reviewer/reviewers/_fallback.py src/code_reviewer/processor.py`
Expected: No new errors

- [ ] **Step 5: Commit**

```bash
git add src/code_reviewer/processor.py
git commit -m "feat: check circuit breaker before launching full review tasks"
```

---

### Task 6: Final validation

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Run linter and formatter**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: Clean (aside from pre-existing E501 violations)

- [ ] **Step 3: Commit any formatting fixes if needed**

```bash
uv run ruff format .
git add -u
git commit -m "style: format circuit breaker code"
```
