from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from code_reviewer.prompts import PromptOverrideError

log = logging.getLogger(__name__)


async def run_with_fallback[T](
    backends: list[str],
    runner: Callable[[str], Awaitable[T]],
    label: str,
    context: str,
) -> T:
    """Try *runner* for each backend in order; re-raise last exception if all fail."""
    last_exc: Exception | None = None
    for i, backend in enumerate(backends):
        try:
            return await runner(backend)
        except PromptOverrideError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
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
    raise last_exc  # type: ignore[misc]
