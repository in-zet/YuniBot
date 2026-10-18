from __future__ import annotations

import asyncio
import logging

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from core.llm import (
    FunctionCallRequest,
    GenerationTurn,
    LlmContent,
    LlmPart,
    OnRetry,
    ToolDeclaration,
    UsageInfo,
)

logger = logging.getLogger(__name__)

# Gemini가 일시 과부하(503)·속도 제한(429)일 때만 재시도한다 — 나머지 4xx(잘못된 요청,
# 인증 등)는 다시 불러도 똑같이 실패하니 바로 올린다. 2026-10-18 실기에서 재시도 없이
# 한 번 실패하면 바로 고정 문구로 답하는 문제를 확인했다(docs/plan.md §5).
_RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 3
_RETRY_BASE_DELAY_SECONDS = 2.0  # 시도마다 2배로 늘린다(2초, 4초, 8초).


def to_provider_content(content: LlmContent) -> types.Content:
    """코어가 만든(함수 실행 결과를 담은) LlmContent를 google-genai Content로 바꾼다.
    모델이 낸 턴(raw가 채워진 LlmContent)은 이 함수를 타지 않고 raw를 그대로 쓴다
    (thought signature를 '받은 그대로' 보존하기 위해 — 2026-10-12 확인한 공식 규칙)."""
    if content.raw is not None:
        return content.raw
    parts: list[types.Part] = []
    for part in content.parts:
        if part.text is not None:
            parts.append(types.Part(text=part.text))
        if part.function_response is not None:
            parts.append(
                types.Part.from_function_response(
                    name=part.function_response.name, response=part.function_response.response
                )
            )
    return types.Content(role=content.role, parts=parts)


def _from_provider_response(response: types.GenerateContentResponse) -> GenerationTurn:
    assert response.candidates, "응답에 candidate가 없다"
    candidate = response.candidates[0]
    model_content = candidate.content
    assert model_content is not None, "candidate에 content가 없다"

    model_parts = model_content.parts or []
    text_parts = [p.text for p in model_parts if p.text]
    function_calls = [
        FunctionCallRequest(
            call_id=p.function_call.id,
            name=p.function_call.name or "",
            args=dict(p.function_call.args or {}),
        )
        for p in model_parts
        if p.function_call is not None
    ]

    usage = response.usage_metadata
    return GenerationTurn(
        content=LlmContent(role="model", parts=[LlmPart(text=t) for t in text_parts], raw=model_content),
        function_calls=function_calls,
        text="\n".join(text_parts) if text_parts else None,
        usage=UsageInfo(
            input_tokens=usage.prompt_token_count or 0 if usage else 0,
            # 사고(thinking) 토큰은 출력 가격에 포함된다(00-core.md "비용과 운영") —
            # 보이는 출력 텍스트보다 많은 경우가 흔해, 빼면 비용/월상한 집행이 실제보다
            # 낮게 잡힌다. 새 필드를 만들지 않고 output_tokens에 바로 더한다.
            output_tokens=(
                (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0) if usage else 0
            ),
        ),
    )


def _to_provider_tool(tools: list[ToolDeclaration]) -> list[types.Tool]:
    if not tools:
        return []
    declarations = [
        types.FunctionDeclaration(
            name=t.name, description=t.description, parameters_json_schema=t.parameters_json_schema
        )
        for t in tools
    ]
    return [types.Tool(function_declarations=declarations)]


class GeminiLlmAdapter:
    """LlmAdapter 프로토콜의 유일한 실제 구현. google.genai.types를 아는 유일한 모듈이다."""

    def __init__(self, *, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

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
        provider_contents = [to_provider_content(c) for c in contents]
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=_to_provider_tool(tools) or None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel(thinking_level)),
        )
        attempt = 0
        while True:
            try:
                response = await self._client.aio.models.generate_content(
                    model=model, contents=provider_contents, config=config
                )
                return _from_provider_response(response)
            except genai_errors.APIError as exc:
                attempt += 1
                if exc.code not in _RETRYABLE_CODES or attempt > _MAX_RETRIES:
                    raise
                delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Gemini %s, 재시도 %d/%d (%.0f초 후): %s", exc.code, attempt, _MAX_RETRIES, delay, exc.message,
                )
                if on_retry is not None:
                    await on_retry(attempt, delay)
                await asyncio.sleep(delay)

    async def aclose(self) -> None:
        await self._client.aio.aclose()
