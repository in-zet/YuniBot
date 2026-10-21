# skills/web-qa/tests/test_web_qa.py
from __future__ import annotations

import asyncio
import importlib.util
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from core.search import SearchResult, SearchSource, SearchUsage
from core.tool_types import ToolContext
from core.usage import UsageService
from services.common.websearch import NoSourcesError, SearchCooldownError

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_web_qa_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


class _FakeWebsearch:
    def __init__(self, *, result: SearchResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.model = "gemini-3.8-flash"
        self.calls: list[dict[str, Any]] = []

    async def search(self, *, query: str, player_id: str, now: datetime) -> SearchResult:
        self.calls.append({"query": query, "player_id": player_id, "now": now})
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


@dataclass(slots=True, kw_only=True)
class _FakeUsageService:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def record(
        self, *, guild_id: str, player_id: str, model: str, kind: str, input_tokens: int, output_tokens: int
    ) -> float:
        self.calls.append(
            {
                "guild_id": guild_id, "player_id": player_id, "model": model, "kind": kind,
                "input_tokens": input_tokens, "output_tokens": output_tokens,
            }
        )
        return 0.0


def _result(*, text: str = "답", sources: list[SearchSource] | None = None) -> SearchResult:
    return SearchResult(
        text=text,
        sources=sources if sources is not None else [SearchSource(title="위키", url="https://example/위키")],
        usage=SearchUsage(input_tokens=100, output_tokens=50),
    )


def _ctx(*, websearch: _FakeWebsearch, usage: _FakeUsageService | None, guild_id: str | None = "g1") -> ToolContext:
    return ToolContext(
        player_id="p1", tags=frozenset(), services={"websearch": websearch}, guild_id=guild_id,
        usage=cast("UsageService | None", usage),
    )


@pytest.mark.asyncio
async def test_search_returns_answer_with_required_sources_display(skill: ModuleType) -> None:
    websearch = _FakeWebsearch(result=_result())
    usage = _FakeUsageService()

    result = await skill.search(_ctx(websearch=websearch, usage=usage), question="월드 엔드는 어떻게 해금해?")

    assert result.error is None
    assert result.data["answer"] == "답"
    assert result.data["sources"] == [{"title": "위키", "url": "https://example/위키"}]
    assert result.display_mode == "required"
    assert result.display is not None
    assert "위키" in result.display.content and "https://example/위키" in result.display.content
    assert len(websearch.calls) == 1
    assert websearch.calls[0]["query"] == "월드 엔드는 어떻게 해금해?"
    assert websearch.calls[0]["player_id"] == "p1"


@pytest.mark.asyncio
async def test_search_records_usage_with_websearch_model(skill: ModuleType) -> None:
    websearch = _FakeWebsearch(result=_result())
    usage = _FakeUsageService()

    await skill.search(_ctx(websearch=websearch, usage=usage, guild_id="g1"), question="질문")

    assert usage.calls == [
        {
            "guild_id": "g1", "player_id": "p1", "model": "gemini-3.8-flash", "kind": "websearch",
            "input_tokens": 100, "output_tokens": 50,
        }
    ]


@pytest.mark.asyncio
async def test_search_dm_usage_scope_is_dm(skill: ModuleType) -> None:
    websearch = _FakeWebsearch(result=_result())
    usage = _FakeUsageService()

    await skill.search(_ctx(websearch=websearch, usage=usage, guild_id=None), question="질문")

    assert usage.calls[0]["guild_id"] == "dm"


@pytest.mark.asyncio
async def test_search_skips_usage_recording_when_ctx_usage_is_none(skill: ModuleType) -> None:
    websearch = _FakeWebsearch(result=_result())

    result = await skill.search(_ctx(websearch=websearch, usage=None), question="질문")

    assert result.error is None


@pytest.mark.asyncio
async def test_search_cooldown_error_becomes_korean_message(skill: ModuleType) -> None:
    websearch = _FakeWebsearch(error=SearchCooldownError())

    result = await skill.search(_ctx(websearch=websearch, usage=None), question="질문")

    assert result.error is not None
    assert result.data == {}


@pytest.mark.asyncio
async def test_search_no_sources_error_becomes_korean_message(skill: ModuleType) -> None:
    websearch = _FakeWebsearch(error=NoSourcesError())

    result = await skill.search(_ctx(websearch=websearch, usage=None), question="질문")

    assert result.error is not None
    assert result.data == {}


@pytest.mark.asyncio
async def test_search_unexpected_failure_becomes_error_telling_not_to_retry(skill: ModuleType) -> None:
    """2026-10-21: Google 503·시간 초과가 도구 예외로 새면 모델이 같은 검색을 되풀이했다."""
    websearch = _FakeWebsearch(error=RuntimeError("503 UNAVAILABLE"))

    result = await skill.search(_ctx(websearch=websearch, usage=None), question="질문")

    assert result.error is not None
    assert "다시 하지 말고" in result.error
    assert len(websearch.calls) == 1


@pytest.mark.asyncio
async def test_search_gives_up_when_total_time_runs_out(skill: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Slow(_FakeWebsearch):
        async def search(self, *, query: str, player_id: str, now: datetime) -> SearchResult:
            await asyncio.sleep(10)
            return await super().search(query=query, player_id=player_id, now=now)

    monkeypatch.setattr(skill, "_SEARCH_TOTAL_SECONDS", 0.01)

    result = await skill.search(_ctx(websearch=_Slow(result=_result()), usage=None), question="질문")

    assert result.error is not None
    assert "다시 하지 말고" in result.error
