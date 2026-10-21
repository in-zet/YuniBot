from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from core.llm import (
    FunctionCallRequest,
    FunctionResultPart,
    LlmContent,
    LlmPart,
    ToolDeclaration,
)
from core.llm_claude import ClaudeLlmAdapter, to_provider_content


@dataclass
class _FakeMessagesApi:
    """client.messages를 대신한다. 테스트가 미리 정해 둔 Message를 돌려준다."""

    captured_kwargs: dict[str, Any] | None = None
    response: Any = None

    async def create(self, **kwargs: Any) -> Any:
        self.captured_kwargs = kwargs
        return self.response


def _fake_message(*, text: str | None, tool_use: ToolUseBlock | None) -> Message:
    content: list[Any] = []
    if text is not None:
        content.append(TextBlock(type="text", text=text, citations=None))
    if tool_use is not None:
        content.append(tool_use)
    return Message(
        id="msg_1", type="message", role="assistant", model="claude-haiku-5-5",
        content=content, stop_reason="tool_use" if tool_use else "end_turn", stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=4),
    )


@pytest.mark.asyncio
async def test_generate_converts_text_only_response() -> None:
    adapter = ClaudeLlmAdapter(api_key="fake")
    fake_messages = _FakeMessagesApi(response=_fake_message(text="안녕", tool_use=None))
    adapter._client.messages = fake_messages  # pyright: ignore[reportAttributeAccessIssue]

    turn = await adapter.generate(
        model="claude-haiku-5-5",
        system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])],
        tools=[],
        thinking_level="low",
    )

    assert turn.text == "안녕"
    assert turn.function_calls == []
    assert turn.usage.input_tokens == 10
    assert turn.usage.output_tokens == 4
    assert fake_messages.captured_kwargs is not None
    # 시스템 프롬프트는 캐시 중단점이 붙은 블록으로 보낸다.
    assert fake_messages.captured_kwargs["system"] == [
        {"type": "text", "text": "규칙", "cache_control": {"type": "ephemeral"}}
    ]
    # thinking_level은 effort로 넘어간다.
    assert fake_messages.captured_kwargs["output_config"] == {"effort": "low"}
    assert fake_messages.captured_kwargs["model"] == "claude-haiku-5-5"


@pytest.mark.asyncio
async def test_generate_converts_tool_use_block() -> None:
    adapter = ClaudeLlmAdapter(api_key="fake")
    tool_use = ToolUseBlock(
        type="tool_use", id="call-1", name="chunithm-border_lookup", input={"chart": "크로노 MAS"},
    )
    fake_messages = _FakeMessagesApi(response=_fake_message(text=None, tool_use=tool_use))
    adapter._client.messages = fake_messages  # pyright: ignore[reportAttributeAccessIssue]

    turn = await adapter.generate(
        model="claude-haiku-5-5",
        system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])],
        tools=[ToolDeclaration(name="chunithm-border_lookup", description="d", parameters_json_schema={"type": "object"})],
        thinking_level="low",
    )

    assert len(turn.function_calls) == 1
    assert turn.function_calls[0] == FunctionCallRequest(
        call_id="call-1", name="chunithm-border_lookup", args={"chart": "크로노 MAS"}
    )
    # 모델 턴 전체(raw)가 보존되어 다음 턴에 그대로 재생할 수 있다.
    assert turn.content.raw is not None

    assert fake_messages.captured_kwargs is not None
    provider_tools = cast("list[dict[str, Any]]", fake_messages.captured_kwargs["tools"])
    assert provider_tools[0]["name"] == "chunithm-border_lookup"
    assert provider_tools[0]["input_schema"] == {"type": "object"}


@pytest.mark.asyncio
async def test_generate_omits_tools_kwarg_when_no_tools_declared() -> None:
    adapter = ClaudeLlmAdapter(api_key="fake")
    fake_messages = _FakeMessagesApi(response=_fake_message(text="답", tool_use=None))
    adapter._client.messages = fake_messages  # pyright: ignore[reportAttributeAccessIssue]

    result = await adapter.generate(
        model="claude-haiku-5-5", system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])], tools=[], thinking_level="low",
    )

    # tools가 없을 때 빈 리스트를 그대로 보내는 대신 아예 생략한다(Gemini 어댑터와 같은 관례).
    import anthropic

    assert result.text == "답"
    assert fake_messages.captured_kwargs is not None
    assert fake_messages.captured_kwargs["tools"] is anthropic.omit


def test_to_provider_content_replays_raw_model_blocks() -> None:
    message = _fake_message(text="안녕", tool_use=None)
    content = LlmContent(role="model", parts=[LlmPart(text="안녕")], raw=message)

    provider_message = to_provider_content(content)

    assert provider_message["role"] == "assistant"
    assert provider_message["content"] == message.content  # 사고 블록이 없으면 그대로


def test_to_provider_content_builds_tool_result_block_keyed_by_call_id() -> None:
    content = LlmContent(
        role="user",
        parts=[LlmPart(function_response=FunctionResultPart(
            name="chunithm-border_lookup", response={"ok": True}, call_id="call-1",
        ))],
    )

    provider_message = to_provider_content(content)

    assert provider_message["role"] == "user"
    blocks = cast("list[dict[str, Any]]", provider_message["content"])
    block = blocks[0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "call-1"
    assert json.loads(cast("str", block["content"])) == {"ok": True}
    assert block["is_error"] is False


def test_to_provider_content_marks_tool_error_result() -> None:
    content = LlmContent(
        role="user",
        parts=[LlmPart(function_response=FunctionResultPart(
            name="chunithm-border_lookup", response={"error": "모르는 명령이에요"}, call_id="call-1",
        ))],
    )

    provider_message = to_provider_content(content)

    blocks = cast("list[dict[str, Any]]", provider_message["content"])
    assert blocks[0]["is_error"] is True


@pytest.mark.asyncio
async def test_generate_counts_cache_tokens_into_input_total() -> None:
    adapter = ClaudeLlmAdapter(api_key="fake")
    message = _fake_message(text="답", tool_use=None)
    message.usage = Usage(
        input_tokens=50, output_tokens=7, cache_read_input_tokens=900, cache_creation_input_tokens=100
    )
    adapter._client.messages = _FakeMessagesApi(response=message)  # pyright: ignore[reportAttributeAccessIssue]

    turn = await adapter.generate(
        model="claude-haiku-5-5", system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])], tools=[], thinking_level="medium",
    )

    # Anthropic의 input_tokens는 캐시 읽기·쓰기를 뺀 값이라 더해야 UsageInfo의 "전체 입력"이 된다.
    assert turn.usage.input_tokens == 1050
    assert turn.usage.cached_input_tokens == 900
    assert turn.usage.output_tokens == 7


def test_tool_result_block_serializes_non_json_values() -> None:
    import datetime

    block = to_provider_content(
        LlmContent(
            role="user",
            parts=[
                LlmPart(
                    function_response=FunctionResultPart(
                        name="t", call_id="c1", response={"when": datetime.date(2026, 10, 21)}
                    )
                )
            ],
        )
    )

    content = cast("list[dict[str, Any]]", block["content"])
    assert json.loads(content[0]["content"]) == {"when": "2026-10-21"}


@pytest.mark.asyncio
async def test_generate_retries_overloaded_and_reports_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import anthropic
    import httpx2

    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("core.claude_retry.asyncio.sleep", _no_sleep)

    overloaded = anthropic.InternalServerError(
        "overloaded", response=httpx2.Response(529, request=httpx2.Request("POST", "https://x")), body=None
    )
    ok = _fake_message(text="답", tool_use=None)

    @dataclass
    class _Flaky:
        remaining: int = 2

        async def create(self, **kwargs: Any) -> Any:
            if self.remaining > 0:
                self.remaining -= 1
                raise overloaded
            return ok

    adapter = ClaudeLlmAdapter(api_key="fake")
    adapter._client.messages = _Flaky()  # pyright: ignore[reportAttributeAccessIssue]
    retries: list[tuple[int, float]] = []

    async def _on_retry(attempt: int, delay: float) -> None:
        retries.append((attempt, delay))

    turn = await adapter.generate(
        model="claude-haiku-5-5", system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])], tools=[], thinking_level="low",
        on_retry=_on_retry,
    )

    assert turn.text == "답"
    assert retries == [(1, 2.0), (2, 4.0)]
    assert sleeps == [2.0, 4.0]


@pytest.mark.asyncio
async def test_generate_does_not_retry_client_errors() -> None:
    import anthropic
    import httpx2

    bad = anthropic.BadRequestError(
        "bad", response=httpx2.Response(400, request=httpx2.Request("POST", "https://x")), body=None
    )

    @dataclass
    class _Bad:
        calls: int = 0

        async def create(self, **kwargs: Any) -> Any:
            self.calls += 1
            raise bad

    adapter = ClaudeLlmAdapter(api_key="fake")
    api = _Bad()
    adapter._client.messages = api  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(anthropic.BadRequestError):
        await adapter.generate(
            model="claude-haiku-5-5", system_instruction="규칙",
            contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])], tools=[], thinking_level="low",
        )

    assert api.calls == 1


@pytest.mark.asyncio
async def test_generate_retries_timeout_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import anthropic
    import httpx2

    async def _no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("core.claude_retry.asyncio.sleep", _no_sleep)

    @dataclass
    class _Hang:
        calls: int = 0

        async def create(self, **kwargs: Any) -> Any:
            self.calls += 1
            raise anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x"))

    adapter = ClaudeLlmAdapter(api_key="fake")
    api = _Hang()
    adapter._client.messages = api  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(anthropic.APITimeoutError):
        await adapter.generate(
            model="claude-haiku-5-5", system_instruction="규칙",
            contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])], tools=[], thinking_level="low",
        )

    assert api.calls == 2  # 첫 시도 + 재시도 한 번


def test_to_provider_content_drops_thinking_blocks_when_replaying_a_model_turn() -> None:
    from anthropic.types import ThinkingBlock

    tool_use = ToolUseBlock(type="tool_use", id="call-1", name="t", input={})
    message = _fake_message(text="먼저 찾아볼게", tool_use=tool_use)
    message.content.insert(0, ThinkingBlock(type="thinking", thinking="", signature="sig"))
    content = LlmContent(role="model", parts=[LlmPart(text="먼저 찾아볼게")], raw=message)

    provider_message = to_provider_content(content)

    assert [block.type for block in cast("list[Any]", provider_message["content"])] == ["text", "tool_use"]
