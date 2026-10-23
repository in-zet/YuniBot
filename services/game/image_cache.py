# services/game/image_cache.py
"""그림 표에 들어가는 그림(재킷·캐릭터 그림·프레임)을 받아 디스크에 캐시한다.

주소 규칙은 chuni-penguin의 `get_jacket_url`과 같다(국제판 기본, 일본 시각 4~7시 점검 때만 일본판).
`get_jacket_url`은 chuni-penguin의 `Song`·`config`에 묶여 있어 그대로 부르지 않고 규칙만 가져왔다.
캐시 파일 이름은 주소의 마지막 부분이다. 받기에 실패한 그림은 None으로 돌려주고 캐시하지 않는다."""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from chuni_penguin.constants import INTERNATIONAL_JACKET_BASE, JACKET_BASE
from PIL import Image

logger = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")
# 작은 그림 파일이라 기본 호출 간격(1초)보다 빠르게 받는다. 호스트별로 센다.
_FETCH_INTERVAL_SECONDS = 0.1
# 한 번에 받는 그림 수.
_FETCH_CONCURRENCY = 8


class _HttpGet(Protocol):
    async def get(self, url: str, *, min_interval_seconds: float | None = None) -> httpx.Response: ...


def jacket_url(jacket: str | None, *, now: datetime | None = None) -> str | None:
    """재킷 파일 이름 → 주소. 이미 전체 주소(Chunimaru가 준 것)면 그대로 쓴다.
    일본 시각 4~7시(CHUNITHM-NET 점검 시간)에는 일본판 주소를 쓴다."""
    if not jacket:
        return None
    if jacket.startswith(("http://", "https://")):
        return jacket
    hour = (now or datetime.now(_JST)).astimezone(_JST).hour
    base = JACKET_BASE if 4 <= hour <= 7 else INTERNATIONAL_JACKET_BASE
    return f"{base}/{jacket}"


def _file_name(url: str) -> str | None:
    name = PurePosixPath(urlparse(url).path).name
    return name or None


def _is_image(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        return False
    return True


class ImageCache:
    """주소 → 그림 바이트. 디스크에 있으면 받지 않는다."""

    def __init__(self, *, http: _HttpGet, directory: Path) -> None:
        self._http = http
        self._directory = directory
        self._semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def fetch(self, url: str | None) -> bytes | None:
        """그림 하나. 주소가 없거나 받기에 실패했거나 그림이 아니면 None."""
        if not url:
            return None
        name = _file_name(url)
        if name is None:
            return None
        path = self._directory / name
        try:
            if path.is_file():
                return path.read_bytes()
        except OSError:
            logger.warning("그림 캐시를 읽지 못했다: %s", path, exc_info=True)
        data = await self._download(url)
        if data is None:
            return None
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError:
            logger.warning("그림 캐시를 쓰지 못했다: %s", path, exc_info=True)
        return data

    async def fetch_many(self, urls: Iterable[str | None]) -> Mapping[str, bytes | None]:
        """같은 주소는 한 번만 받는다. 주소가 없는 항목은 결과에 넣지 않는다."""
        unique = list(dict.fromkeys(url for url in urls if url))
        results = await asyncio.gather(*(self.fetch(url) for url in unique))
        return dict(zip(unique, results, strict=True))

    async def _download(self, url: str) -> bytes | None:
        async with self._semaphore:
            try:
                response = await self._http.get(url, min_interval_seconds=_FETCH_INTERVAL_SECONDS)
            except httpx.HTTPError:
                logger.warning("그림을 받지 못했다: %s", url, exc_info=True)
                return None
        if response.status_code != 200 or not _is_image(response.content):
            logger.warning("그림이 아닌 응답: %s (%s)", url, response.status_code)
            return None
        return response.content
