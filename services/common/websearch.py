# services/common/websearch.py
from __future__ import annotations

from datetime import datetime

from core.cooldown import CooldownTracker
from core.llm import OnRetry
from core.search import SearchAdapter, SearchResult

# 플레이어별이지 서버별이 아니라서(00-core.md "websearch" 서비스 설명), CooldownTracker가
# 보는 (guild_id, player_id) 키의 guild_id 자리에 고정값을 넣어 서버/DM을 가리지 않게 한다.
_COOLDOWN_SCOPE = "websearch"


class SearchCooldownError(ValueError):
    def __init__(self) -> None:
        super().__init__("검색은 조금 더 기다려야 한다")


class NoSourcesError(ValueError):
    def __init__(self) -> None:
        super().__init__("검색 결과에서 출처를 찾지 못했다")


class WebsearchService:
    """질문을 검색해 내용과 출처를 돌려준다. 실제 제공자 호출은 core/search_*.py 어댑터가
    하고, 이 서비스는 플레이어별 쿨다운과 '출처 없음' 검사만 한다(00-core.md의 서비스 역할:
    "Gemini 검색 도구 호출, 출처 추출, 응답당 검색 횟수 상한" 중 응답당 횟수 상한은 도구의
    max_calls_per_turn이 맡는다 — web-qa.md Part 3)."""

    def __init__(self, *, adapter: SearchAdapter, model: str, cooldown_seconds: int = 15) -> None:
        self._adapter = adapter
        self._model = model
        self._cooldown_seconds = cooldown_seconds
        self._cooldown = CooldownTracker()

    @property
    def model(self) -> str:
        """사용량 기록(usage.record의 model 인자)에 쓸 검색 모델 이름. 호출부가 설정값을 따로
        들고 있지 않도록 서비스가 들고 있는 값을 그대로 돌려준다."""
        return self._model

    async def search(
        self, *, query: str, player_id: str, now: datetime, on_retry: OnRetry | None = None
    ) -> SearchResult:
        if not self._cooldown.check(
            guild_id=_COOLDOWN_SCOPE, player_id=player_id, cooldown_seconds=self._cooldown_seconds, now=now
        ):
            raise SearchCooldownError()
        result = await self._adapter.search(model=self._model, query=query, on_retry=on_retry)
        if not result.sources:
            raise NoSourcesError()
        return result

    async def aclose(self) -> None:
        await self._adapter.aclose()
