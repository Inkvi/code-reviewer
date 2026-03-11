import asyncio

import pytest

from code_reviewer.prompts import PromptOverrideError
from code_reviewer.reviewers._fallback import run_with_fallback


def test_success_on_first_backend() -> None:
    async def runner(backend: str) -> str:
        return f"ok-{backend}"

    result = asyncio.run(run_with_fallback(["claude"], runner, "test", "ctx"))
    assert result == "ok-claude"


def test_fallback_to_second_backend() -> None:
    call_log: list[str] = []

    async def runner(backend: str) -> str:
        call_log.append(backend)
        if backend == "gemini":
            raise RuntimeError("gemini down")
        return f"ok-{backend}"

    result = asyncio.run(run_with_fallback(["gemini", "claude"], runner, "test", "ctx"))
    assert result == "ok-claude"
    assert call_log == ["gemini", "claude"]


def test_all_fail_reraises_last_exception() -> None:
    async def runner(backend: str) -> str:
        raise RuntimeError(f"{backend} failed")

    with pytest.raises(RuntimeError, match="codex failed"):
        asyncio.run(run_with_fallback(["gemini", "codex"], runner, "test", "ctx"))


def test_single_element_list_no_fallback() -> None:
    async def runner(backend: str) -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(run_with_fallback(["claude"], runner, "test", "ctx"))


def test_prompt_override_error_propagates_immediately() -> None:
    call_log: list[str] = []

    async def runner(backend: str) -> str:
        call_log.append(backend)
        raise PromptOverrideError("bad prompt")

    with pytest.raises(PromptOverrideError, match="bad prompt"):
        asyncio.run(run_with_fallback(["gemini", "claude"], runner, "test", "ctx"))
    # Should NOT try the second backend
    assert call_log == ["gemini"]


def test_fallback_chain_of_three() -> None:
    call_log: list[str] = []

    async def runner(backend: str) -> str:
        call_log.append(backend)
        if backend in ("gemini", "claude"):
            raise RuntimeError(f"{backend} down")
        return f"ok-{backend}"

    result = asyncio.run(run_with_fallback(["gemini", "claude", "codex"], runner, "test", "ctx"))
    assert result == "ok-codex"
    assert call_log == ["gemini", "claude", "codex"]
