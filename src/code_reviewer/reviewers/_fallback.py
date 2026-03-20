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
        if models is not None:
            model = models.get(backend)
            opened, reason = is_open(backend, model)
            if opened:
                log.warning(
                    "skipping %s/%s (circuit open: %s) %s",
                    backend,
                    model or "default",
                    reason,
                    context,
                )
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
    # If all were skipped, try the one whose circuit closes soonest.
    if skipped and last_exc is None:
        from code_reviewer.reviewers._circuit_breaker import _circuits, _now

        def _open_until(b: str) -> float:
            key = (b, models.get(b) if models else None)
            state = _circuits.get(key)
            return state.open_until.timestamp() if state else _now().timestamp()

        soonest = min(skipped, key=_open_until)
        log.warning(
            "all backends circuit-open, trying soonest-closing: %s %s",
            soonest,
            context,
        )
        return await runner(soonest)

    raise last_exc  # type: ignore[misc]
