import io
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from PIL import Image

from services.chunithm.image_cache import ImageCache, jacket_url

_JST = ZoneInfo("Asia/Tokyo")


def _png(color: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeHttp:
    def __init__(self, responses: dict[str, httpx.Response | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def get(self, url: str, *, min_interval_seconds: float | None = None) -> httpx.Response:
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def test_jacket_url_uses_international_base_by_day() -> None:
    now = datetime(2026, 10, 20, 15, 0, tzinfo=_JST)
    assert jacket_url("abc.jpg", now=now) == "https://chunithm-net-eng.com/mobile/img/abc.jpg"


@pytest.mark.parametrize("hour", [4, 7])
def test_jacket_url_uses_japan_base_during_maintenance(hour: int) -> None:
    now = datetime(2026, 10, 20, hour, 30, tzinfo=_JST)
    assert jacket_url("abc.jpg", now=now) == (
        "https://new.chunithm-net.com/chuni-mobile/html/mobile/img/abc.jpg"
    )


@pytest.mark.parametrize(("hour", "minute"), [(3, 59), (8, 0)])
def test_jacket_url_outside_maintenance_edges(hour: int, minute: int) -> None:
    url = jacket_url("a.jpg", now=datetime(2026, 10, 20, hour, minute, tzinfo=_JST))
    assert url == "https://chunithm-net-eng.com/mobile/img/a.jpg"


def test_jacket_url_keeps_full_url_and_handles_none() -> None:
    assert jacket_url("https://example.test/x.webp") == "https://example.test/x.webp"
    assert jacket_url(None) is None
    assert jacket_url("") is None


async def test_fetch_downloads_then_serves_from_disk(tmp_path: Path) -> None:
    data = _png()
    http = _FakeHttp({"https://img.test/a/b.png": httpx.Response(200, content=data)})
    cache = ImageCache(http=http, directory=tmp_path / "images")

    assert await cache.fetch("https://img.test/a/b.png") == data
    assert (tmp_path / "images" / "b.png").read_bytes() == data
    assert await cache.fetch("https://img.test/a/b.png") == data
    assert http.calls == ["https://img.test/a/b.png"]  # 두 번째는 디스크에서


async def test_fetch_failure_returns_none_and_is_not_cached(tmp_path: Path) -> None:
    http = _FakeHttp(
        {
            "https://img.test/404.png": httpx.Response(404, content=b"no"),
            "https://img.test/html.png": httpx.Response(200, content=b"<html>error</html>"),
            "https://img.test/boom.png": httpx.ConnectError("boom"),
        }
    )
    cache = ImageCache(http=http, directory=tmp_path)

    for name in ("404", "html", "boom"):
        assert await cache.fetch(f"https://img.test/{name}.png") is None
    assert list(tmp_path.iterdir()) == []


async def test_fetch_none_or_nameless_url_makes_no_request(tmp_path: Path) -> None:
    http = _FakeHttp({})
    cache = ImageCache(http=http, directory=tmp_path)

    assert await cache.fetch(None) is None
    assert await cache.fetch("https://img.test/") is None
    assert http.calls == []


async def test_fetch_many_deduplicates_and_maps_by_url(tmp_path: Path) -> None:
    http = _FakeHttp(
        {
            "https://img.test/a.png": httpx.Response(200, content=_png("red")),
            "https://img.test/b.png": httpx.Response(500, content=b""),
        }
    )
    cache = ImageCache(http=http, directory=tmp_path)

    result = await cache.fetch_many(
        ["https://img.test/a.png", None, "https://img.test/a.png", "https://img.test/b.png"]
    )

    assert set(result) == {"https://img.test/a.png", "https://img.test/b.png"}
    assert result["https://img.test/a.png"] is not None
    assert result["https://img.test/b.png"] is None
    assert sorted(http.calls) == ["https://img.test/a.png", "https://img.test/b.png"]
