import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from code_reviewer.models import PRCandidate
from code_reviewer.reviewers.codex_agents_sdk import (
    _build_agent_model_settings,
    _extract_result_markdown,
    _extract_token_usage,
    _invoke_runner_sync,
    _load_agents_sdk,
)


def _sample_pr(*, is_local: bool = False) -> PRCandidate:
    return PRCandidate(
        owner="polymerdao",
        repo="obul",
        number=64,
        url="https://github.com/polymerdao/obul/pull/64",
        title="test",
        author_login="alice",
        base_ref="main",
        head_sha="deadbeef",
        updated_at="2026-02-27T20:00:00Z",
        is_local=is_local,
    )


# --- _extract_token_usage ---


def test_extract_token_usage_from_object_with_usage_attr() -> None:
    result = MagicMock()
    result.usage = MagicMock()
    result.usage.input_tokens = 100
    result.usage.output_tokens = 50
    usage = _extract_token_usage(result)
    assert usage is not None
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50


def test_extract_token_usage_from_dict_usage() -> None:
    result = MagicMock()
    result.usage = {"input_tokens": 200, "output_tokens": 80}
    usage = _extract_token_usage(result)
    assert usage is not None
    assert usage.input_tokens == 200
    assert usage.output_tokens == 80


def test_extract_token_usage_from_dict_result() -> None:
    result = {"usage": {"input_tokens": 300, "output_tokens": 100}}
    usage = _extract_token_usage(result)
    assert usage is not None
    assert usage.input_tokens == 300


def test_extract_token_usage_no_usage() -> None:
    result = MagicMock(spec=[])  # No attributes
    usage = _extract_token_usage(result)
    assert usage is None


def test_extract_token_usage_zero_tokens() -> None:
    result = MagicMock()
    result.usage = {"input_tokens": 0, "output_tokens": 0}
    usage = _extract_token_usage(result)
    assert usage is None


# --- _extract_result_markdown ---


def test_extract_result_markdown_from_final_output() -> None:
    result = MagicMock()
    result.final_output = "### Findings\n- No material findings."
    assert _extract_result_markdown(result) == "### Findings\n- No material findings."


def test_extract_result_markdown_from_output() -> None:
    result = MagicMock(spec=["output"])
    result.output = "review text"
    assert _extract_result_markdown(result) == "review text"


def test_extract_result_markdown_from_string() -> None:
    assert _extract_result_markdown("direct string result") == "direct string result"


def test_extract_result_markdown_from_dict() -> None:
    result = {"final_output": "from dict"}
    assert _extract_result_markdown(result) == "from dict"


def test_extract_result_markdown_empty() -> None:
    result = MagicMock(spec=[])
    assert _extract_result_markdown(result) == ""


# --- _invoke_runner_sync ---


def test_invoke_runner_sync_with_run_sync() -> None:
    runner = MagicMock()
    agent = MagicMock()
    runner.run_sync.return_value = "result"
    result = _invoke_runner_sync(runner, agent, "prompt")
    assert result == "result"


def test_invoke_runner_sync_with_run() -> None:
    runner = MagicMock(spec=["run"])
    agent = MagicMock()
    runner.run.return_value = "result"
    result = _invoke_runner_sync(runner, agent, "prompt")
    assert result == "result"


def test_invoke_runner_sync_no_method_raises() -> None:
    runner = MagicMock(spec=[])
    agent = MagicMock()
    with pytest.raises(RuntimeError, match="does not expose run"):
        _invoke_runner_sync(runner, agent, "prompt")


# --- _build_agent_model_settings ---


def test_build_agent_model_settings_none_effort() -> None:
    mod = MagicMock()
    result = _build_agent_model_settings(mod, None)
    assert result is None


def test_build_agent_model_settings_no_model_settings_cls() -> None:
    mod = MagicMock(spec=[])  # No ModelSettings
    result = _build_agent_model_settings(mod, "high")
    assert result is None


# --- _load_agents_sdk ---


def test_load_agents_sdk_missing_raises() -> None:
    with pytest.raises(RuntimeError, match="requires the OpenAI Agents SDK"):
        # Temporarily remove agents/openai_agents from sys.modules
        saved = {}
        for name in ["agents", "openai_agents"]:
            if name in sys.modules:
                saved[name] = sys.modules.pop(name)
        try:
            import importlib
            # Force fresh import attempt
            importlib.invalidate_caches()
            _load_agents_sdk()
        finally:
            sys.modules.update(saved)


# --- run_codex_review_via_agents_sdk ---


def test_run_codex_review_via_agents_sdk_timeout(monkeypatch) -> None:
    from code_reviewer.reviewers.codex_agents_sdk import run_codex_review_via_agents_sdk

    async def slow_sync(*args, **kwargs):  # noqa: ANN002,ANN003
        import asyncio
        await asyncio.sleep(100)

    monkeypatch.setattr(
        "code_reviewer.reviewers.codex_agents_sdk._run_agents_codex_review_sync",
        lambda *a, **kw: slow_sync(),
    )
    monkeypatch.setattr(
        "code_reviewer.reviewers.codex_agents_sdk.asyncio.to_thread",
        lambda fn, *a, **kw: slow_sync(),
    )

    async def _run():  # noqa: ANN202
        return await run_codex_review_via_agents_sdk(
            _sample_pr(), Path("/tmp"), timeout_seconds=0,
            model="gpt-5.3-codex",
        )

    result = asyncio.run(_run())
    assert result.status == "error"
    assert "timed out" in result.error
