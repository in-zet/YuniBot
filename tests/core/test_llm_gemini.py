from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from google.genai import errors as genai_errors
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


@dataclass
class _FlakyModelsApi:
    """먼저 던질 예외들을 순서대로 내고, 그다음부터는 response를 돌려준다(재시도 시험용)."""

    to_raise: list[Exception]
    response: Any = None
    call_count: int = field(default=0)

    async def generate_content(self, **kwargs: Any) -> Any:
        del kwargs
        self.call_count += 1
        if self.to_raise:
            raise self.to_raise.pop(0)
        return self.response


def _server_error(code: int) -> genai_errors.ServerError:
    return genai_errors.ServerError(code, {"message": "overloaded", "status": "UNAVAILABLE"})


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
async def test_generate_leaves_thought_parts_out_of_answer_text() -> None:
    adapter = GeminiLlmAdapter(api_key="fake")
    content = types.Content(
        role="model", parts=[types.Part(text="규칙을 정리한다", thought=True), types.Part(text="안녕")]
    )
    response = types.GenerateContentResponse(
        candidates=[types.Candidate(content=content)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=1, candidates_token_count=1),
    )
    adapter._client.aio._models = _FakeModelsApi(response=response)  # pyright: ignore[reportAttributeAccessIssue]

    turn = await adapter.generate(
        model="gemini-3.8-flash",
        system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])],
        tools=[],
        thinking_level="low",
    )

    assert turn.text == "안녕"


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
    # 회귀: 설계서 "비용과 운영"은 사고 토큰이 출력 가격에 포함된다고 적는다.
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


@pytest.mark.asyncio
async def test_generate_retries_on_503_and_notifies_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """실기 2026-10-18: 재시도 없이 한 번 503을 맞으면 바로 고정 문구로 답했다. 503은
    두 번 재시도해 성공하면 그대로 결과를 돌려주고, 재시도 직전마다 on_retry를 불러야 한다."""
    monkeypatch.setattr("core.gemini_retry.asyncio.sleep", lambda _delay: _NoopAwaitable())
    adapter = GeminiLlmAdapter(api_key="fake")
    fake_models = _FlakyModelsApi(
        to_raise=[_server_error(503), _server_error(503)],
        response=_fake_response(text="안녕", function_call=None),
    )
    adapter._client.aio._models = fake_models  # pyright: ignore[reportAttributeAccessIssue]

    retries: list[tuple[int, float]] = []

    async def on_retry(attempt: int, delay: float) -> None:
        retries.append((attempt, delay))

    turn = await adapter.generate(
        model="gemini-3.8-flash", system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])], tools=[], thinking_level="low",
        on_retry=on_retry,
    )

    assert turn.text == "안녕"
    assert fake_models.call_count == 3
    assert retries == [(1, 2.0), (2, 4.0)]


@pytest.mark.asyncio
async def test_generate_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.gemini_retry.asyncio.sleep", lambda _delay: _NoopAwaitable())
    adapter = GeminiLlmAdapter(api_key="fake")
    fake_models = _FlakyModelsApi(to_raise=[_server_error(503)] * 10)
    adapter._client.aio._models = fake_models  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(genai_errors.ServerError):
        await adapter.generate(
            model="gemini-3.8-flash", system_instruction="규칙",
            contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])], tools=[], thinking_level="low",
        )
    assert fake_models.call_count == 4  # 첫 시도 + 재시도 3번


@pytest.mark.asyncio
async def test_generate_retries_deadline_exceeded_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-10-19: gemini-3.8-flash가 응답 없이 멈췄다. 요청 타임아웃에 걸린 504는 다시 불러도
    같은 시간을 또 기다릴 가능성이 커서 503보다 적게(한 번만) 재시도한다."""
    monkeypatch.setattr("core.gemini_retry.asyncio.sleep", lambda _delay: _NoopAwaitable())
    adapter = GeminiLlmAdapter(api_key="fake")
    fake_models = _FlakyModelsApi(to_raise=[_server_error(504)] * 10)
    adapter._client.aio._models = fake_models  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(genai_errors.ServerError):
        await adapter.generate(
            model="gemini-3.8-flash", system_instruction="규칙",
            contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])], tools=[], thinking_level="low",
        )
    assert fake_models.call_count == 2  # 첫 시도 + 재시도 1번


@dataclass
class _ModelAwareApi:
    """모델 이름별로 정해 둔 결과(예외 또는 응답)를 돌려주고, 불린 모델 이름을 순서대로 적는다."""

    outcomes: dict[str, Any]
    called_models: list[str] = field(default_factory=list)

    async def generate_content(self, **kwargs: Any) -> Any:
        model = kwargs["model"]
        self.called_models.append(model)
        outcome = self.outcomes[model]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def _generate_with_fallback(api: _ModelAwareApi, *, fallback_model: str | None) -> Any:
    adapter = GeminiLlmAdapter(api_key="fake", fallback_model=fallback_model)
    adapter._client.aio._models = api  # pyright: ignore[reportAttributeAccessIssue]
    return await adapter.generate(
        model="main", system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])], tools=[], thinking_level="low",
    )


@pytest.mark.asyncio
async def test_deadline_exceeded_goes_straight_to_fallback_model() -> None:
    """2026-10-20: 보조 모델이 있으면 504를 같은 모델로 다시 기다리지 않고(재시도 0) 바로 넘긴다."""
    api = _ModelAwareApi({"main": _server_error(504), "backup": _fake_response(text="보조 답", function_call=None)})

    turn = await _generate_with_fallback(api, fallback_model="backup")

    assert api.called_models == ["main", "backup"]
    assert turn.text == "보조 답"
    assert turn.model == "backup"


@pytest.mark.asyncio
async def test_client_side_timeout_goes_to_fallback_model_like_504() -> None:
    """2026-10-21: 클라이언트 타이머가 서버 504보다 먼저 끊어 날것 TimeoutError가 올라와도 보조 모델로 넘긴다."""
    api = _ModelAwareApi({"main": TimeoutError(), "backup": _fake_response(text="보조 답", function_call=None)})

    turn = await _generate_with_fallback(api, fallback_model="backup")

    assert api.called_models == ["main", "backup"]
    assert turn.model == "backup"


@pytest.mark.asyncio
async def test_client_side_timeout_without_fallback_retries_once() -> None:
    api = _ModelAwareApi({"main": TimeoutError()})

    with pytest.raises(genai_errors.APIError):
        await _generate_with_fallback(api, fallback_model=None)
    assert api.called_models == ["main", "main"]


@pytest.mark.asyncio
async def test_exhausted_503_retries_then_fallback_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.gemini_retry.asyncio.sleep", lambda _delay: _NoopAwaitable())
    api = _ModelAwareApi({"main": _server_error(503), "backup": _fake_response(text="보조 답", function_call=None)})

    turn = await _generate_with_fallback(api, fallback_model="backup")

    assert api.called_models == ["main"] * 4 + ["backup"]  # 첫 시도 + 재시도 3번, 그다음 보조
    assert turn.model == "backup"


@pytest.mark.asyncio
async def test_main_model_success_does_not_touch_fallback() -> None:
    api = _ModelAwareApi({"main": _fake_response(text="메인 답", function_call=None)})

    turn = await _generate_with_fallback(api, fallback_model="backup")

    assert api.called_models == ["main"]
    assert turn.model is None  # 요청한 모델이 그대로 답했다


@pytest.mark.asyncio
async def test_client_error_does_not_use_fallback_model() -> None:
    """잘못된 요청(400)은 모델을 바꿔도 똑같이 실패한다 — 보조 모델로 넘기지 않는다."""
    bad_request = genai_errors.ClientError(400, {"message": "bad", "status": "INVALID_ARGUMENT"})
    api = _ModelAwareApi({"main": bad_request, "backup": _fake_response(text="x", function_call=None)})

    with pytest.raises(genai_errors.ClientError):
        await _generate_with_fallback(api, fallback_model="backup")
    assert api.called_models == ["main"]


@pytest.mark.asyncio
async def test_fallback_failure_raises_the_fallback_error() -> None:
    api = _ModelAwareApi({"main": _server_error(504), "backup": _server_error(504)})

    with pytest.raises(genai_errors.ServerError):
        await _generate_with_fallback(api, fallback_model="backup")
    assert api.called_models == ["main", "backup", "backup"]  # 보조 모델의 504는 기존 규칙대로 한 번 재시도


@pytest.mark.asyncio
async def test_without_fallback_model_keeps_deadline_retry_once() -> None:
    api = _ModelAwareApi({"main": _server_error(504)})

    with pytest.raises(genai_errors.ServerError):
        await _generate_with_fallback(api, fallback_model=None)
    assert api.called_models == ["main", "main"]


def test_adapters_set_request_timeout() -> None:
    """타임아웃이 없으면 모델이 멈출 때 멘션 처리가 끝없이 기다린다."""
    from core.search_gemini import GeminiSearchAdapter

    llm = GeminiLlmAdapter(api_key="fake")
    search = GeminiSearchAdapter(api_key="fake")
    assert llm._client._api_client._http_options.timeout == 20_000  # pyright: ignore[reportPrivateUsage]
    assert search._client._api_client._http_options.timeout == 60_000  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_generate_does_not_retry_non_retryable_client_error() -> None:
    adapter = GeminiLlmAdapter(api_key="fake")
    not_found = genai_errors.ClientError(404, {"message": "model not found", "status": "NOT_FOUND"})
    fake_models = _FlakyModelsApi(to_raise=[not_found])
    adapter._client.aio._models = fake_models  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(genai_errors.ClientError):
        await adapter.generate(
            model="gemini-3.8-flash", system_instruction="규칙",
            contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])], tools=[], thinking_level="low",
        )
    assert fake_models.call_count == 1


class _NoopAwaitable:
    def __await__(self):
        return iter(())


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


@pytest.mark.asyncio
async def test_generate_reports_cached_input_tokens() -> None:
    adapter = GeminiLlmAdapter(api_key="fake")
    content = types.Content(role="model", parts=[types.Part(text="답")])
    usage = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=100, candidates_token_count=4, cached_content_token_count=60,
    )
    response = types.GenerateContentResponse(candidates=[types.Candidate(content=content)], usage_metadata=usage)
    adapter._client.aio._models = _FakeModelsApi(response=response)  # pyright: ignore[reportAttributeAccessIssue]

    turn = await adapter.generate(
        model="gemini-3.8-flash", system_instruction="규칙",
        contents=[LlmContent(role="user", parts=[LlmPart(text="질문")])], tools=[], thinking_level="low",
    )

    assert (turn.usage.input_tokens, turn.usage.cached_input_tokens) == (100, 60)
