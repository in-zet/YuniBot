# core/search_claude.py
from __future__ import annotations

import anthropic
from anthropic.types import (
    Message,
    TextBlock,
    WebSearchResultBlock,
    WebSearchTool20260318Param,
    WebSearchToolResultBlock,
)

from core.llm import OnRetry
from core.search import SearchResult, SearchSource, SearchUsage

# llm_claude.py와 같은 이유로 넉넉히 잡아 둔다(core/llm_claude.py의 _DEFAULT_MAX_TOKENS 참고).
_DEFAULT_MAX_TOKENS = 4096


def _extract_sources(message: Message) -> list[SearchSource]:
    sources: list[SearchSource] = []
    for block in message.content:
        if not isinstance(block, WebSearchToolResultBlock):
            continue
        if not isinstance(block.content, list):
            continue  # WebSearchToolResultError — 출처 없음으로 취급한다.
        for item in block.content:
            if isinstance(item, WebSearchResultBlock):
                sources.append(SearchSource(title=item.title, url=item.url))
    return sources


def _from_provider_response(message: Message) -> SearchResult:
    text_parts = [block.text for block in message.content if isinstance(block, TextBlock)]
    usage = message.usage
    return SearchResult(
        text="\n".join(text_parts) if text_parts else None,
        sources=_extract_sources(message),
        usage=SearchUsage(input_tokens=usage.input_tokens or 0, output_tokens=usage.output_tokens or 0),
    )


class ClaudeSearchAdapter:
    """SearchAdapter 프로토콜을 Claude의 web_search 서버 도구로 구현한다. 검색은 Claude가
    서버 쪽에서 직접 하므로(우리 쪽 함수 호출 루프를 안 거친다) 메인 에이전트 어댑터
    (core/llm_claude.py)와 호출 모양이 다르다 — 별도 모듈로 둔다."""

    def __init__(self, *, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def search(self, *, model: str, query: str, on_retry: OnRetry | None = None) -> SearchResult:
        del on_retry  # Claude 쪽 재시도는 core/llm_claude.py와 같이 아직 미구현(docs/plan.md §5).
        message = await self._client.messages.create(
            model=model,
            max_tokens=_DEFAULT_MAX_TOKENS,
            messages=[{"role": "user", "content": query}],
            tools=[WebSearchTool20260318Param(name="web_search", type="web_search_20260318")],
        )
        return _from_provider_response(message)

    async def aclose(self) -> None:
        await self._client.close()
