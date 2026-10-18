from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from core.search import SearchResult, SearchSource
from services.common.websearch import (
    NoSourcesError,
    SearchCooldownError,
    WebsearchService,
)


class _FakeAdapter:
    def __init__(self, result: SearchResult) -> None:
        self._result = result
        self.calls: list[str] = []
        self.closed = False

    async def search(self, *, model: str, query: str, on_retry: Any = None) -> SearchResult:
        del model, on_retry
        self.calls.append(query)
        return self._result

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_search_returns_result_when_sources_present() -> None:
    adapter = _FakeAdapter(SearchResult(text="답", sources=[SearchSource(title="위키", url="https://a")]))
    service = WebsearchService(adapter=adapter, model="gemini-3.8-flash")
    now = datetime(2026, 10, 18, 10, 0, 0, tzinfo=UTC)

    result = await service.search(query="질문", player_id="p1", now=now)

    assert result.text == "답"
    assert adapter.calls == ["질문"]


@pytest.mark.asyncio
async def test_search_raises_no_sources_error() -> None:
    adapter = _FakeAdapter(SearchResult(text="모르겠다", sources=[]))
    service = WebsearchService(adapter=adapter, model="gemini-3.8-flash")
    now = datetime(2026, 10, 18, 10, 0, 0, tzinfo=UTC)

    with pytest.raises(NoSourcesError):
        await service.search(query="질문", player_id="p1", now=now)


@pytest.mark.asyncio
async def test_search_raises_cooldown_error_within_window() -> None:
    adapter = _FakeAdapter(SearchResult(text="답", sources=[SearchSource(title="위키", url="https://a")]))
    service = WebsearchService(adapter=adapter, model="gemini-3.8-flash", cooldown_seconds=15)
    now = datetime(2026, 10, 18, 10, 0, 0, tzinfo=UTC)

    await service.search(query="첫 질문", player_id="p1", now=now)
    with pytest.raises(SearchCooldownError):
        await service.search(query="두번째 질문", player_id="p1", now=now)

    assert adapter.calls == ["첫 질문"]


@pytest.mark.asyncio
async def test_search_cooldown_is_per_player() -> None:
    adapter = _FakeAdapter(SearchResult(text="답", sources=[SearchSource(title="위키", url="https://a")]))
    service = WebsearchService(adapter=adapter, model="gemini-3.8-flash", cooldown_seconds=15)
    now = datetime(2026, 10, 18, 10, 0, 0, tzinfo=UTC)

    await service.search(query="p1의 질문", player_id="p1", now=now)
    await service.search(query="p2의 질문", player_id="p2", now=now)

    assert adapter.calls == ["p1의 질문", "p2의 질문"]


@pytest.mark.asyncio
async def test_aclose_closes_adapter() -> None:
    adapter = _FakeAdapter(SearchResult(text=None, sources=[]))
    service = WebsearchService(adapter=adapter, model="gemini-3.8-flash")

    await service.aclose()

    assert adapter.closed is True
