from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from anthropic.types import (
    CitationsWebSearchResultLocation,
    Message,
    ServerToolUsage,
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
    assert tools[0]["max_uses"] == 5  # 한 호출에서 검색 횟수에 상한을 건다


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


@pytest.mark.asyncio
async def test_search_reports_billed_web_search_requests() -> None:
    adapter = ClaudeSearchAdapter(api_key="fake")
    message = _fake_message(text="답변", result_block=None)
    message.usage = Usage(input_tokens=20, output_tokens=8, server_tool_use=ServerToolUsage(web_search_requests=2, web_fetch_requests=0))
    adapter._client.messages = _FakeMessagesApi(response=message)  # pyright: ignore[reportAttributeAccessIssue]

    result = await adapter.search(model="claude-haiku-5-5", query="질문")

    assert result.usage.search_requests == 2


@pytest.mark.asyncio
async def test_search_retries_once_on_overload(monkeypatch: pytest.MonkeyPatch) -> None:
    import anthropic
    import httpx2

    async def _no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("core.claude_retry.asyncio.sleep", _no_sleep)

    @dataclass
    class _Overloaded:
        calls: int = 0

        async def create(self, **kwargs: Any) -> Any:
            self.calls += 1
            raise anthropic.InternalServerError(
                "overloaded", response=httpx2.Response(529, request=httpx2.Request("POST", "https://x")), body=None
            )

    adapter = ClaudeSearchAdapter(api_key="fake")
    api = _Overloaded()
    adapter._client.messages = api  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(anthropic.InternalServerError):
        await adapter.search(model="claude-haiku-5-5", query="질문")

    assert api.calls == 2  # 검색은 재시도를 한 번만 한다


@pytest.mark.asyncio
async def test_search_prefers_sources_cited_in_the_answer_over_all_results() -> None:
    """검색 결과로 받은 페이지가 많아도 답에 인용된 출처만 쓴다(같은 주소는 한 번)."""
    adapter = ClaudeSearchAdapter(api_key="fake")
    results = [
        WebSearchResultBlock(type="web_search_result", title=f"결과 {i}", url=f"https://e/{i}", encrypted_content="e")
        for i in range(6)
    ]
    cited: list[Any] = [
        CitationsWebSearchResultLocation(
            type="web_search_result_location", cited_text="t", encrypted_index="i", title="결과 2", url="https://e/2",
        ),
        CitationsWebSearchResultLocation(
            type="web_search_result_location", cited_text="t", encrypted_index="i", title="결과 2", url="https://e/2",
        ),
        CitationsWebSearchResultLocation(
            type="web_search_result_location", cited_text="t", encrypted_index="i", title="결과 4", url="https://e/4",
        ),
    ]
    blocks: list[Any] = [
        WebSearchToolResultBlock(type="web_search_tool_result", tool_use_id="c1", content=results),
        TextBlock(type="text", text="답변", citations=cited),
    ]
    message = Message(
        id="msg_1", type="message", role="assistant", model="claude-haiku-5-5", content=blocks,
        stop_reason="end_turn", stop_sequence=None, usage=Usage(input_tokens=20, output_tokens=8),
    )
    adapter._client.messages = _FakeMessagesApi(response=message)  # pyright: ignore[reportAttributeAccessIssue]

    result = await adapter.search(model="claude-haiku-5-5", query="질문")

    assert result.sources == [SearchSource(title="결과 2", url="https://e/2"), SearchSource(title="결과 4", url="https://e/4")]
