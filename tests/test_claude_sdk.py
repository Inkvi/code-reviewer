from code_reviewer.reviewers.claude_sdk import _block_to_dict


class _FakeBlock:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_block_to_dict_text() -> None:
    block = _FakeBlock(type="text", text="hello world")
    result = _block_to_dict(block)
    assert result == {"type": "text", "text": "hello world"}


def test_block_to_dict_tool_use() -> None:
    block = _FakeBlock(type="tool_use", name="read_file", input={"path": "/tmp"}, id="tu_123")
    result = _block_to_dict(block)
    expected = {"type": "tool_use", "name": "read_file", "input": {"path": "/tmp"}, "id": "tu_123"}
    assert result == expected


def test_block_to_dict_tool_result() -> None:
    block = _FakeBlock(type="tool_result", tool_use_id="tu_123", content="file contents")
    result = _block_to_dict(block)
    assert result == {"type": "tool_result", "tool_use_id": "tu_123", "content": "file contents"}


def test_block_to_dict_thinking() -> None:
    block = _FakeBlock(type="thinking", thinking="let me think...")
    result = _block_to_dict(block)
    assert result == {"type": "thinking", "thinking": "let me think..."}


def test_block_to_dict_unknown() -> None:
    block = _FakeBlock(type="custom")
    result = _block_to_dict(block)
    assert result["type"] == "custom"
    assert "text" in result
