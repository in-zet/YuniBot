from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from anthropic.types import (
    Message,
    TextBlock,
    Usage,
    WebSearchResultBlock,
    WebSearchToolResultBlock,
    WebSearchToolResultError,
)

from core.search import SearchSource
from core.search_claude import ClaudeSearchAdapter


@dataclass
class _FakeMessagesApi:
    captured_kwargs: dict[str, Any] | None = None
    response: Any = None

    async def create(self, **kwargs: Any) -> Any:
        self.captured_kwargs = kwargs
        return self.response


def _fake_message(*, text: str | None, result_block: Any | None) -> Message:
    content: list[Any] = []
    if text is not None:
        content.append(TextBlock(type="text", text=text, citations=None))
    if result_block is not None:
        content.append(result_block)
    return Message(
        id="msg_1", type="message", role="assistant", model="claude-haiku-5-5",
        content=content, stop_reason="end_turn", stop_sequence=None,
        usage=Usage(input_tokens=20, output_tokens=8),
    )


@pytest.mark.asyncio
async def test_search_extracts_sources_from_web_search_tool_result_block() -> None:
    adapter = ClaudeSearchAdapter(api_key="fake")
    result_block = WebSearchToolResultBlock(
        type="web_search_tool_result",
        tool_use_id="call-1",
        content=[
            WebSearchResultBlock(
                type="web_search_result", title="위키", url="https://wikiwiki.jp/a", encrypted_content="enc",
            )
        ],
    )
    fake_messages = _FakeMessagesApi(response=_fake_message(text="답변", result_block=result_block))
    adapter._client.messages = fake_messages  # pyright: ignore[reportAttributeAccessIssue]

    result = await adapter.search(model="claude-haiku-5-5", query="질문")

    assert result.text == "답변"
    assert result.sources == [SearchSource(title="위키", url="https://wikiwiki.jp/a")]
    assert result.usage.input_tokens == 20
    assert result.usage.output_tokens == 8
    assert fake_messages.captured_kwargs is not None
    tools = fake_messages.captured_kwargs["tools"]
    assert tools[0]["type"] == "web_search_20260318"


@pytest.mark.asyncio
async def test_search_returns_no_sources_when_result_is_error() -> None:
    adapter = ClaudeSearchAdapter(api_key="fake")
    result_block = WebSearchToolResultBlock(
        type="web_search_tool_result",
        tool_use_id="call-1",
        content=WebSearchToolResultError(type="web_search_tool_result_error", error_code="max_uses_exceeded"),
    )
    fake_messages = _FakeMessagesApi(response=_fake_message(text="모르겠다", result_block=result_block))
    adapter._client.messages = fake_messages  # pyright: ignore[reportAttributeAccessIssue]

    result = await adapter.search(model="claude-haiku-5-5", query="질문")

    assert result.sources == []
