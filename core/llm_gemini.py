from __future__ import annotations

import logging

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from core.gemini_retry import (
    DEADLINE_EXCEEDED_CODES,
    LLM_REQUEST_TIMEOUT_SECONDS,
    call_with_retry,
    http_options,
    is_retryable,
)
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
    # thought=True인 part는 모델의 생각이라 답에 싣지 않는다(raw에는 그대로 남는다).
    text_parts = [p.text for p in model_parts if p.text and not p.thought]
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

    def __init__(self, *, api_key: str, fallback_model: str | None = None) -> None:
        self._client = genai.Client(api_key=api_key, http_options=http_options(LLM_REQUEST_TIMEOUT_SECONDS))
        # 메인 모델이 일시 오류로 끝내 실패하면 같은 요청을 이 모델로 한 번 더 보낸다(2026-10-20).
        self._fallback_model = fallback_model or None

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
        # Pillow가 깔리면 SDK의 입력 타입에 PIL 이미지가 끼어, 리스트가 불변이라 타입을 직접 넓혀 준다.
        provider_contents: list[types.ContentUnion] = [to_provider_content(c) for c in contents]
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=_to_provider_tool(tools) or None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel(thinking_level)),
        )

        async def _call_model(name: str) -> types.GenerateContentResponse:
            return await self._client.aio.models.generate_content(
                model=name, contents=provider_contents, config=config
            )

        fallback = self._fallback_model
        if fallback is None or fallback == model:
            response = await call_with_retry(lambda: _call_model(model), on_retry=on_retry)
            return _from_provider_response(response)

        try:
            response = await call_with_retry(
                lambda: _call_model(model), on_retry=on_retry, skip_retry_codes=DEADLINE_EXCEEDED_CODES
            )
            return _from_provider_response(response)
        except genai_errors.APIError as exc:
            if not is_retryable(exc):
                raise
            logger.warning("%s 호출이 %s로 실패해 보조 모델 %s로 넘긴다: %s", model, exc.code, fallback, exc.message)
        response = await call_with_retry(lambda: _call_model(fallback), on_retry=on_retry)
        turn = _from_provider_response(response)
        turn.model = fallback
        return turn

    async def aclose(self) -> None:
        await self._client.aio.aclose()
