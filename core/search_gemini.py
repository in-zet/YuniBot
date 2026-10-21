# core/search_gemini.py
from __future__ import annotations

from google import genai
from google.genai import types

from core.gemini_retry import (
    SEARCH_MAX_RETRIES,
    SEARCH_REQUEST_TIMEOUT_SECONDS,
    call_with_retry,
    http_options,
)
from core.llm import OnRetry
from core.search import SearchResult, SearchSource, SearchUsage


def _extract_sources(response: types.GenerateContentResponse) -> list[SearchSource]:
    if not response.candidates:
        return []
    grounding = response.candidates[0].grounding_metadata
    if grounding is None or not grounding.grounding_chunks:
        return []
    sources: list[SearchSource] = []
    for chunk in grounding.grounding_chunks:
        if chunk.web is not None and chunk.web.uri:
            sources.append(SearchSource(title=chunk.web.title or chunk.web.uri, url=chunk.web.uri))
    return sources


def _from_provider_response(response: types.GenerateContentResponse) -> SearchResult:
    text: str | None = None
    if response.candidates:
        model_content = response.candidates[0].content
        if model_content is not None and model_content.parts:
            text_parts = [p.text for p in model_content.parts if p.text]
            text = "\n".join(text_parts) if text_parts else None

    usage = response.usage_metadata
    return SearchResult(
        text=text,
        sources=_extract_sources(response),
        usage=SearchUsage(
            input_tokens=usage.prompt_token_count or 0 if usage else 0,
            output_tokens=(
                (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0) if usage else 0
            ),
        ),
    )


class GeminiSearchAdapter:
    """SearchAdapter 프로토콜을 Gemini의 Google 검색 도구로 구현한다. 메인 에이전트 호출
    (core/llm_gemini.py)과 별도 client를 쓴다 — 검색은 함수 호출 도구를 안 쓰므로 둘을
    합칠 이유가 없다(web-qa.md "미확인 사항" — 합쳐 쓸 수 있게 되면 그때 바꾼다)."""

    def __init__(self, *, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key, http_options=http_options(SEARCH_REQUEST_TIMEOUT_SECONDS))

    async def search(self, *, model: str, query: str, on_retry: OnRetry | None = None) -> SearchResult:
        config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])

        async def _call() -> types.GenerateContentResponse:
            return await self._client.aio.models.generate_content(model=model, contents=query, config=config)

        response = await call_with_retry(_call, on_retry=on_retry, max_retries=SEARCH_MAX_RETRIES)
        return _from_provider_response(response)

    async def aclose(self) -> None:
        await self._client.aio.aclose()
