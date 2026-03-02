from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pr_reviewer.models import PRCandidate, ReviewerOutput


def _load_agents_sdk() -> Any:
    try:
        import agents as openai_agents  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        try:
            import openai_agents  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "codex_backend=agents_sdk requires the OpenAI Agents SDK Python package. "
                "Install it and configure OPENAI_API_KEY."
            ) from exc
    return openai_agents


def _invoke_runner_sync(runner: Any, agent: Any, prompt: str) -> Any:
    if hasattr(runner, "run_sync"):
        try:
            return runner.run_sync(agent, input=prompt)
        except TypeError:
            return runner.run_sync(agent, prompt)

    if hasattr(runner, "run"):
        try:
            result = runner.run(agent, input=prompt)
        except TypeError:
            result = runner.run(agent, prompt)
        if inspect.isawaitable(result):
            return asyncio.run(result)
        return result

    raise RuntimeError("OpenAI Agents SDK Runner does not expose run/run_sync")


def _extract_result_markdown(result: Any) -> str:
    for attr in ("final_output", "output", "result"):
        if hasattr(result, attr):
            value = getattr(result, attr)
            if isinstance(value, str) and value.strip():
                return value.strip()

    if isinstance(result, str) and result.strip():
        return result.strip()

    if isinstance(result, dict):
        for key in ("final_output", "output", "result"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _build_agent_model_settings(openai_agents: Any, reasoning_effort: str | None) -> Any:
    if reasoning_effort is None or not hasattr(openai_agents, "ModelSettings"):
        return None

    model_settings_cls = openai_agents.ModelSettings
    try:
        parameters = inspect.signature(model_settings_cls).parameters
    except (TypeError, ValueError):
        parameters = {}

    if "reasoning" in parameters:
        return model_settings_cls(reasoning={"effort": reasoning_effort})
    if "reasoning_effort" in parameters:
        return model_settings_cls(reasoning_effort=reasoning_effort)

    # Fallback for versions where signature introspection is unavailable.
    try:
        return model_settings_cls(reasoning={"effort": reasoning_effort})
    except Exception:  # noqa: BLE001
        return model_settings_cls(reasoning_effort=reasoning_effort)


def _run_agents_codex_review_sync(
    pr: PRCandidate, workspace: Path, model: str, reasoning_effort: str | None
) -> str:
    openai_agents = _load_agents_sdk()
    if not hasattr(openai_agents, "Agent") or not hasattr(openai_agents, "Runner"):
        raise RuntimeError("OpenAI Agents SDK does not provide Agent/Runner")

    prompt = (
        f"Review pull request {pr.url}.\n"
        f"Repository workspace: {workspace}\n"
        f"Base branch: origin/{pr.base_ref}\n\n"
        "Focus only on actionable bugs, regressions, and missing tests. "
        "Return concise markdown with exactly:\n"
        "### Findings\n"
        "- [P1|P2|P3] path[:line] - issue. Impact. Recommended fix.\n"
        "### Test Gaps\n"
        "- missing tests\n"
        "If no findings, write '- No material findings.' under Findings and '- None noted.' "
        "under Test Gaps."
    )
    instructions = (
        "You are a strict code reviewer. Use repository context and git diff against the provided "
        "base branch. Do not add filler, and do not invent evidence."
    )
    agent_kwargs: dict[str, Any] = {
        "name": "Codex PR Reviewer",
        "instructions": instructions,
        "model": model,
    }
    model_settings = _build_agent_model_settings(openai_agents, reasoning_effort)
    if model_settings is not None:
        agent_kwargs["model_settings"] = model_settings

    agent = openai_agents.Agent(**agent_kwargs)
    result = _invoke_runner_sync(openai_agents.Runner, agent, prompt)
    markdown = _extract_result_markdown(result)
    if not markdown:
        raise RuntimeError("OpenAI Agents SDK returned an empty review")
    return markdown


async def run_codex_review_via_agents_sdk(
    pr: PRCandidate,
    workspace: Path,
    timeout_seconds: int,
    model: str,
    reasoning_effort: str | None = None,
) -> ReviewerOutput:
    started = datetime.now(UTC)
    try:
        markdown = await asyncio.wait_for(
            asyncio.to_thread(
                _run_agents_codex_review_sync,
                pr,
                workspace,
                model,
                reasoning_effort,
            ),
            timeout=timeout_seconds,
        )
        status = "ok"
        error = None
        stderr = ""
    except TimeoutError:
        markdown = ""
        status = "error"
        stderr = f"OpenAI Agents SDK codex review timed out after {timeout_seconds}s"
        error = stderr
    except Exception as exc:  # noqa: BLE001
        markdown = ""
        status = "error"
        stderr = str(exc)
        error = str(exc)

    ended = datetime.now(UTC)
    return ReviewerOutput(
        reviewer="codex",
        status=status,
        markdown=markdown,
        stdout=markdown,
        stderr=stderr,
        error=error,
        started_at=started,
        ended_at=ended,
    )
