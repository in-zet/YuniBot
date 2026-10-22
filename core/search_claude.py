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

from core.claude_retry import (
    SEARCH_MAX_RETRIES,
    SEARCH_REQUEST_TIMEOUT_SECONDS,
    call_with_retry,
)
from core.llm import OnRetry
from core.search import SearchResult, SearchSource, SearchUsage

# llm_claude.py와 같은 이유로 넉넉히 잡아 둔다(core/llm_claude.py의 _DEFAULT_MAX_TOKENS 참고).
_DEFAULT_MAX_TOKENS = 4096
# 한 호출에서 모델이 검색할 수 있는 최대 횟수. 상한이 없으면 검색을 여러 번 해 입력이 7만 토큰 넘게 불고
# 검색마다 요금이 붙는다(2026-10-21 실측: 출처 130건).
_MAX_SEARCHES_PER_CALL = 5


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
    server_tool_use = getattr(usage, "server_tool_use", None)
    return SearchResult(
        text="\n".join(text_parts) if text_parts else None,
        sources=_extract_sources(message),
        usage=SearchUsage(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            search_requests=getattr(server_tool_use, "web_search_requests", 0) or 0,
        ),
    )


class ClaudeSearchAdapter:
    """SearchAdapter 프로토콜을 Claude의 web_search 서버 도구로 구현한다. 검색은 Claude가
    서버 쪽에서 직접 하므로(우리 쪽 함수 호출 루프를 안 거친다) 메인 에이전트 어댑터
    (core/llm_claude.py)와 호출 모양이 다르다 — 별도 모듈로 둔다."""

    def __init__(self, *, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=SEARCH_REQUEST_TIMEOUT_SECONDS, max_retries=0
        )

    async def search(self, *, model: str, query: str, on_retry: OnRetry | None = None) -> SearchResult:

        async def _call() -> Message:
            return await self._client.messages.create(
                model=model,
                max_tokens=_DEFAULT_MAX_TOKENS,
                messages=[{"role": "user", "content": query}],
                tools=[
                    WebSearchTool20260318Param(
                        name="web_search", type="web_search_20260318", max_uses=_MAX_SEARCHES_PER_CALL
                    )
                ],
            )

        message = await call_with_retry(_call, on_retry=on_retry, max_retries=SEARCH_MAX_RETRIES)
        return _from_provider_response(message)

    async def aclose(self) -> None:
        await self._client.close()
