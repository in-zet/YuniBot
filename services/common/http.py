from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import anysqlite
import hishel
import httpx
from aiolimiter import AsyncLimiter

_DEFAULT_TTL_SECONDS = 3600.0


class HttpService:
    """공개 웹 페이지/JSON을 가져온다. 캐시와 ETag 재검증은 hishel이, 호스트별 호출
    간격 제한은 aiolimiter가 한다 — 둘 다 벤더링된 third_party 코드가 이미
    의존하는 라이브러리라 새 의존성을 더하지 않는다."""

    def __init__(self, *, client: httpx.AsyncClient, min_interval_seconds: float = 1.0) -> None:
        self._client = client
        self._min_interval = min_interval_seconds
        self._limiters: dict[tuple[str, float], AsyncLimiter] = {}

    def _limiter_for(self, url: str, interval: float | None = None) -> AsyncLimiter:
        interval = self._min_interval if interval is None else interval
        key = (urlparse(url).netloc, interval)
        if key not in self._limiters:
            # 간격이 0이면(테스트) 사실상 무제한으로 둔다 — AsyncLimiter는 time_period<=0을 거부한다.
            period = interval if interval > 0 else 0.001
            self._limiters[key] = AsyncLimiter(1, period)
        return self._limiters[key]

    async def get(self, url: str, *, min_interval_seconds: float | None = None) -> httpx.Response:
        """`min_interval_seconds`를 주면 이 호출만 그 간격(호스트별)으로 센다. 작은 그림 파일을
        여러 장 받을 때처럼 기본 간격이 너무 느린 곳에 쓴다."""
        async with self._limiter_for(url, min_interval_seconds):
            return await self._client.get(url)

    async def get_json(self, url: str) -> Any:
        response = await self.get(url)
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()


async def build_http_service(*, cache_db_path: Path, min_interval_seconds: float = 1.0) -> HttpService:
    """`cache_db_path`는 코어 DB(`data/yuni.db`)와 분리된 캐시 전용 SQLite 파일이다
    (순전히 캐시라 지워도 안전하다). hishel이 그 파일 안의 캐시 스키마를 알아서 관리한다."""
    connection = await anysqlite.connect(str(cache_db_path))
    storage = hishel.AsyncSQLiteStorage(connection=connection, ttl=_DEFAULT_TTL_SECONDS)
    controller = hishel.Controller(
        cacheable_methods=["GET"], cacheable_status_codes=[200], allow_heuristics=False
    )
    client = hishel.AsyncCacheClient(storage=storage, controller=controller)
    return HttpService(client=client, min_interval_seconds=min_interval_seconds)
