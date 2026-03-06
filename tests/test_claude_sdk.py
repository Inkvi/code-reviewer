import sys
import types
from unittest.mock import MagicMock

from code_reviewer.models import PRCandidate


# Create mock claude_agent_sdk module since it's not installed in test env.
def _ensure_mock_sdk() -> types.ModuleType:
    if "claude_agent_sdk" not in sys.modules:
        mod = types.ModuleType("claude_agent_sdk")

        class TextBlock:
            def __init__(self, text: str) -> None:
                self.text = text

        class AssistantMessage:
            def __init__(self, content: list) -> None:
                self.content = content

        class ResultMessage:
            def __init__(self, result: str | None = None, usage: dict | None = None, total_cost_usd: float | None = None) -> None:  # noqa: E501
                self.result = result
                self.usage = usage
                self.total_cost_usd = total_cost_usd

        class ClaudeAgentOptions:
            def __init__(self, **kwargs) -> None:  # noqa: ANN003
                for k, v in kwargs.items():
                    setattr(self, k, v)

        async def query(prompt, options):  # noqa: ANN001
            return
            yield  # Make it an async generator  # noqa: RET503

        mod.TextBlock = TextBlock
        mod.AssistantMessage = AssistantMessage
        mod.ResultMessage = ResultMessage
        mod.ClaudeAgentOptions = ClaudeAgentOptions
        mod.query = query
        sys.modules["claude_agent_sdk"] = mod
    return sys.modules["claude_agent_sdk"]


_ensure_mock_sdk()

from code_reviewer.reviewers.claude_sdk import (  # noqa: E402
    _build_local_review_prompt,
    _collect_text_from_assistant,
    _extract_token_usage,
)


def test_collect_text_from_assistant_single_block() -> None:
    sdk = _ensure_mock_sdk()
    msg = sdk.AssistantMessage([sdk.TextBlock("hello")])
    assert _collect_text_from_assistant(msg) == "hello"


def test_collect_text_from_assistant_multiple_blocks() -> None:
    sdk = _ensure_mock_sdk()
    msg = sdk.AssistantMessage([sdk.TextBlock("hello"), sdk.TextBlock("world")])
    assert _collect_text_from_assistant(msg) == "hello\nworld"


def test_collect_text_from_assistant_non_text_block() -> None:
    sdk = _ensure_mock_sdk()
    non_text = MagicMock()
    non_text.__class__ = type("ToolUseBlock", (), {})
    msg = sdk.AssistantMessage([non_text, sdk.TextBlock("text")])
    assert _collect_text_from_assistant(msg) == "text"


def test_extract_token_usage_valid() -> None:
    sdk = _ensure_mock_sdk()
    msg = sdk.ResultMessage(
        result="ok",
        usage={"input_tokens": 100, "output_tokens": 50},
        total_cost_usd=0.005,
    )
    usage = _extract_token_usage(msg)
    assert usage is not None
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    assert usage.cost_usd == 0.005


def test_extract_token_usage_no_usage() -> None:
    sdk = _ensure_mock_sdk()
    msg = sdk.ResultMessage(result="ok")
    usage = _extract_token_usage(msg)
    assert usage is None


def test_extract_token_usage_non_dict_usage() -> None:
    sdk = _ensure_mock_sdk()
    msg = sdk.ResultMessage(result="ok")
    msg.usage = "not a dict"
    usage = _extract_token_usage(msg)
    assert usage is None


def test_extract_token_usage_zero_tokens_returns_none() -> None:
    sdk = _ensure_mock_sdk()
    msg = sdk.ResultMessage(
        result="ok",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    usage = _extract_token_usage(msg)
    assert usage is None


def test_build_local_review_prompt_branch_mode() -> None:
    pr = PRCandidate(
        owner="local", repo="repo", number=0,
        url="/path/to/repo", title="branch review",
        author_login="user", base_ref="main",
        head_sha="abc123", updated_at="",
        is_local=True, review_mode="branch",
    )
    prompt = _build_local_review_prompt(pr)
    assert "git diff main...abc123" in prompt
    assert "branch review" in prompt


def test_build_local_review_prompt_uncommitted_mode() -> None:
    pr = PRCandidate(
        owner="local", repo="repo", number=0,
        url="/path/to/repo", title="uncommitted changes",
        author_login="user", base_ref="HEAD",
        head_sha="abc123", updated_at="",
        is_local=True, review_mode="uncommitted",
    )
    prompt = _build_local_review_prompt(pr)
    assert "git diff HEAD" in prompt
    assert "untracked" in prompt.lower() or "ls-files" in prompt
