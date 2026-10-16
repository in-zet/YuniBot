from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from google.genai import types

from core.llm import FunctionCallRequest, LlmContent, LlmPart, ToolDeclaration
from core.llm_gemini import GeminiLlmAdapter


@dataclass
class _FakeModelsApi:
    """client.aio.models를 대신한다. 테스트가 미리 정해 둔 GenerateContentResponse를 돌려준다."""

    captured_kwargs: dict[str, Any] | None = None
    response: Any = None

    async def generate_content(self, **kwargs: Any) -> Any:
        self.captured_kwargs = kwargs
        return self.response


def _fake_response(*, text: str | None, function_call: types.FunctionCall | None) -> Any:
    parts = []
    if text is not None:
        parts.append(types.Part(text=text))
    if function_call is not None:
        parts.append(types.Part(function_call=function_call, thought_signature=b"sig-bytes"))
    content = types.Content(role="model", parts=parts)
    candidate = types.Candidate(content=content)
    usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=4)
    return types.GenerateContentResponse(candidates=[candidate], usage_metadata=usage)


@pytest.mark.asyncio
async def test_generate_converts_text_only_response() -> None:
    adapter = GeminiLlmAdapter(api_key="fake")
    fake_models = _FakeModelsApi(response=_fake_response(text="안녕", function_call=None))
    # google-genai 2.29.0에서 AsyncClient.models는 setter 없는 프로퍼티라
    # 뒤에 있는 _models를 직접 바꿔 끼운다.
    adapter._client.aio._models = fake_models  # pyright: ignore[reportAttributeAccessIssue]

    turn = await adapter.generate(
        model="gemini-3.8-flash",
        system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])],
        tools=[],
        thinking_level="low",
    )

    assert turn.text == "안녕"
    assert turn.function_calls == []
    assert turn.usage.input_tokens == 10
    assert turn.usage.output_tokens == 4
    # 자동 함수 호출을 껐는지(AFC 끄기) 확인
    assert fake_models.captured_kwargs is not None
    config = fake_models.captured_kwargs["config"]
    assert config.automatic_function_calling.disable is True


@pytest.mark.asyncio
async def test_generate_converts_function_call_and_preserves_thought_signature() -> None:
    adapter = GeminiLlmAdapter(api_key="fake")
    call = types.FunctionCall(id="call-1", name="chunithm-border_lookup", args={"chart": "크로노 MAS"})
    fake_models = _FakeModelsApi(response=_fake_response(text=None, function_call=call))
    adapter._client.aio._models = fake_models  # pyright: ignore[reportAttributeAccessIssue]

    turn = await adapter.generate(
        model="gemini-3.8-flash",
        system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])],
        tools=[ToolDeclaration(name="chunithm-border_lookup", description="d", parameters_json_schema={"type": "object"})],
        thinking_level="low",
    )

    assert len(turn.function_calls) == 1
    assert turn.function_calls[0] == FunctionCallRequest(
        call_id="call-1", name="chunithm-border_lookup", args={"chart": "크로노 MAS"}
    )
    # 모델 턴 전체(raw)가 보존되어, 다음 턴에 그대로 돌려주면 thought_signature가 살아남는다.
    raw = turn.content.raw
    assert raw is not None
    raw_parts = raw.parts
    assert raw_parts is not None
    assert raw_parts[0].thought_signature == b"sig-bytes"


@pytest.mark.asyncio
async def test_generate_folds_thinking_tokens_into_output_tokens() -> None:
    # I2 회귀: 설계서 "비용과 운영"은 사고 토큰이 출력 가격에 포함된다고 적는다.
    # thoughts_token_count를 무시하면 비용/월상한 집행이 실제보다 낮게 잡힌다.
    adapter = GeminiLlmAdapter(api_key="fake")
    content = types.Content(role="model", parts=[types.Part(text="답")])
    candidate = types.Candidate(content=content)
    usage = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=10, candidates_token_count=4, thoughts_token_count=7,
    )
    response = types.GenerateContentResponse(candidates=[candidate], usage_metadata=usage)
    fake_models = _FakeModelsApi(response=response)
    adapter._client.aio._models = fake_models  # pyright: ignore[reportAttributeAccessIssue]

    turn = await adapter.generate(
        model="gemini-3.8-flash",
        system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])],
        tools=[],
        thinking_level="low",
    )

    assert turn.usage.input_tokens == 10
    assert turn.usage.output_tokens == 4 + 7  # candidates + thoughts


def test_to_provider_content_builds_function_response_part() -> None:
    from core.llm import FunctionResultPart
    from core.llm_gemini import to_provider_content

    content = LlmContent(
        role="user",
        parts=[LlmPart(function_response=FunctionResultPart(name="chunithm-border_lookup", response={"ok": True}))],
    )
    provider_content = to_provider_content(content)
    assert provider_content.role == "user"
    assert provider_content.parts is not None
    function_response = provider_content.parts[0].function_response
    assert function_response is not None
    assert function_response.name == "chunithm-border_lookup"
    assert function_response.response == {"ok": True}
