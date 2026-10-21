# core/llm_claude.py
from __future__ import annotations

import json
from typing import Any, cast

import anthropic
from anthropic.types import (
    Message,
    MessageParam,
    TextBlock,
    TextBlockParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlock,
)

from core.claude_retry import LLM_REQUEST_TIMEOUT_SECONDS, call_with_retry
from core.llm import (
    FunctionCallRequest,
    FunctionResultPart,
    GenerationTurn,
    LlmContent,
    LlmPart,
    OnRetry,
    ToolDeclaration,
    UsageInfo,
)

# Claude Messages API는 호출마다 최대 출력 토큰 수를 반드시 받는다(Gemini엔 없는 요건).
# 이 봇의 답은 짧은 대화 한마디라 여유 있게 잡아 둔다.
_DEFAULT_MAX_TOKENS = 4096


def to_provider_content(content: LlmContent) -> MessageParam:
    """코어가 만든(함수 실행 결과를 담은) LlmContent를 Claude MessageParam으로 바꾼다.
    모델이 낸 턴(raw가 채워진 LlmContent)은 이 함수를 타지 않고 raw(실제 Message)를 그대로
    재생한다 — Claude도 확장 사고(thinking) 블록은 받은 그대로 돌려줘야 하므로, Gemini
    어댑터의 thought-signature 보존과 같은 이유로 raw를 우선한다."""
    if content.raw is not None:
        message: Message = content.raw
        return {"role": "assistant", "content": message.content}

    blocks: list[Any] = []
    for part in content.parts:
        if part.text is not None:
            blocks.append(TextBlockParam(type="text", text=part.text))
        if part.function_response is not None:
            blocks.append(_to_tool_result_block(part.function_response))
    return {"role": "user", "content": blocks}


def _to_tool_result_block(result: FunctionResultPart) -> ToolResultBlockParam:
    # Claude는 도구 결과를 이름이 아니라 호출 ID(tool_use_id)로 짝짓는다 — 같은 도구를
    # 한 턴에 병렬로 두 번 부른 경우 이름만으론 어느 결과가 어느 호출 것인지 구분 못 한다.
    # call_id는 core/agent.py가 FunctionCallRequest.call_id를 그대로 넘겨 채운다.
    return {
        "type": "tool_result",
        "tool_use_id": result.call_id or "",
        "content": json.dumps(result.response, ensure_ascii=False, default=str),
        "is_error": "error" in result.response,
    }


def _from_provider_response(response: Message) -> GenerationTurn:
    text_parts = [block.text for block in response.content if isinstance(block, TextBlock)]
    function_calls = [
        FunctionCallRequest(call_id=block.id, name=block.name, args=dict(block.input or {}))
        for block in response.content
        if isinstance(block, ToolUseBlock)
    ]

    usage = response.usage
    # Anthropic의 input_tokens는 캐시에서 읽은 몫·캐시에 쓴 몫을 뺀 값이다. UsageInfo.input_tokens는
    # 캐시 읽기를 포함한 전체 입력이라(core/llm.py) 둘을 더해 맞춘다. 캐시 쓰기는 일반 입력 가격의
    # 1.25배인데 따로 세지 않고 일반 입력으로 센다(차이가 작다).
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return GenerationTurn(
        content=LlmContent(role="model", parts=[LlmPart(text=t) for t in text_parts], raw=response),
        function_calls=function_calls,
        text="\n".join(text_parts) if text_parts else None,
        usage=UsageInfo(
            input_tokens=(usage.input_tokens or 0) + cache_read + cache_write,
            output_tokens=usage.output_tokens or 0,
            cached_input_tokens=cache_read,
        ),
    )


def _to_provider_tools(tools: list[ToolDeclaration]) -> list[ToolParam]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters_json_schema}
        for t in tools
    ]


class ClaudeLlmAdapter:
    """LlmAdapter 프로토콜을 Anthropic SDK로 구현한다. anthropic 패키지를 아는 유일한 모듈이다.

    thinking_level은 Claude의 `output_config.effort`(low/medium/high)로 넘긴다. 확장 사고 예산
    (`thinking.type=enabled`)은 이 모델이 받지 않고, 사고는 모델이 effort에 맞춰 알아서 켠다.
    사고 블록은 raw 재생(to_provider_content)으로 그대로 돌려보낸다.

    시스템 프롬프트(와 그 앞의 도구 목록)에 프롬프트 캐시 중단점을 둔다 — 한 멘션이 도구를 부르며
    여러 번 왕복할 때 같은 앞부분을 다시 읽는다."""

    def __init__(self, *, api_key: str) -> None:
        # SDK 자체 재시도는 끈다(core/claude_retry.py가 맡는다).
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=LLM_REQUEST_TIMEOUT_SECONDS, max_retries=0
        )

    async def generate(
        self,
        *,
        model: str,
        system_instruction: str,
        contents: list[LlmContent],
        tools: list[ToolDeclaration],
        thinking_level: str,
        on_retry: OnRetry | None = None,
    ) -> GenerationTurn:
        provider_messages = [to_provider_content(c) for c in contents]
        system: list[TextBlockParam] = [
            {"type": "text", "text": system_instruction, "cache_control": {"type": "ephemeral"}}
        ]

        async def _call() -> Message:
            return await self._client.messages.create(
                model=model,
                max_tokens=_DEFAULT_MAX_TOKENS,
                system=system,
                messages=provider_messages,
                tools=_to_provider_tools(tools) if tools else anthropic.omit,
                output_config={"effort": cast("Any", thinking_level)},
            )

        response = await call_with_retry(_call, on_retry=on_retry)
        return _from_provider_response(response)

    async def aclose(self) -> None:
        await self._client.close()
