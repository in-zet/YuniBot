from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from core.search import SearchSource
from core.search_gemini import GeminiSearchAdapter


@dataclass
class _FakeModelsApi:
    captured_kwargs: dict[str, Any] | None = None
    response: Any = None

    async def generate_content(self, **kwargs: Any) -> Any:
        self.captured_kwargs = kwargs
        return self.response


@dataclass
class _FlakyModelsApi:
    to_raise: list[Exception]
    response: Any = None
    call_count: int = field(default=0)

    async def generate_content(self, **kwargs: Any) -> Any:
        del kwargs
        self.call_count += 1
        if self.to_raise:
            raise self.to_raise.pop(0)
        return self.response


def _fake_response(*, text: str | None, sources: list[tuple[str, str]]) -> Any:
    content = types.Content(role="model", parts=[types.Part(text=text)] if text else [])
    chunks = [
        types.GroundingChunk(web=types.GroundingChunkWeb(uri=url, title=title)) for title, url in sources
    ]
    grounding = types.GroundingMetadata(grounding_chunks=chunks) if sources else None
    candidate = types.Candidate(content=content, grounding_metadata=grounding)
    usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=12, candidates_token_count=6)
    return types.GenerateContentResponse(candidates=[candidate], usage_metadata=usage)


@pytest.mark.asyncio
async def test_search_extracts_sources_from_grounding_metadata() -> None:
    adapter = GeminiSearchAdapter(api_key="fake")
    fake_models = _FakeModelsApi(
        response=_fake_response(text="월드 엔드는 ...", sources=[("위키", "https://wikiwiki.jp/a")])
    )
    adapter._client.aio._models = fake_models  # pyright: ignore[reportAttributeAccessIssue]

    result = await adapter.search(model="gemini-3.8-flash", query="월드 엔드 해금 조건")

    assert result.text == "월드 엔드는 ..."
    assert result.sources == [SearchSource(title="위키", url="https://wikiwiki.jp/a")]
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 6
    assert fake_models.captured_kwargs is not None
    config = fake_models.captured_kwargs["config"]
    assert config.tools[0].google_search is not None


@pytest.mark.asyncio
async def test_search_returns_no_sources_when_grounding_metadata_absent() -> None:
    adapter = GeminiSearchAdapter(api_key="fake")
    fake_models = _FakeModelsApi(response=_fake_response(text="모르겠다", sources=[]))
    adapter._client.aio._models = fake_models  # pyright: ignore[reportAttributeAccessIssue]

    result = await adapter.search(model="gemini-3.8-flash", query="아무거나")

    assert result.sources == []


@pytest.mark.asyncio
async def test_search_retries_on_503_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.gemini_retry.asyncio.sleep", lambda _delay: _NoopAwaitable())
    adapter = GeminiSearchAdapter(api_key="fake")
    fake_models = _FlakyModelsApi(
        to_raise=[genai_errors.ServerError(503, {"message": "overloaded", "status": "UNAVAILABLE"})],
        response=_fake_response(text="답", sources=[("출처", "https://example.com")]),
    )
    adapter._client.aio._models = fake_models  # pyright: ignore[reportAttributeAccessIssue]

    retries: list[tuple[int, float]] = []

    async def on_retry(attempt: int, delay: float) -> None:
        retries.append((attempt, delay))

    result = await adapter.search(model="gemini-3.8-flash", query="질문", on_retry=on_retry)

    assert result.text == "답"
    assert retries == [(1, 2.0)]


class _NoopAwaitable:
    def __await__(self):
        return iter(())
