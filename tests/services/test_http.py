import email.utils
import time

import httpx
import pytest

from services.common.http import HttpService


@pytest.fixture
def call_counter() -> list[httpx.Request]:
    return []


def _cached_handler(calls: list[httpx.Request]) -> httpx.AsyncBaseTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        # 실제 원본 서버는 항상 Date를 보낸다(RFC 9110). 없으면 hishel이 나이를 계산하지 못해 재검증한다.
        return httpx.Response(
            200,
            headers={
                "Cache-Control": "max-age=3600",
                "ETag": '"v1"',
                "Date": email.utils.formatdate(time.time(), usegmt=True),
            },
            json={"hits": len(calls)},
        )

    return httpx.MockTransport(handler)


async def test_get_json_returns_parsed_body(call_counter: list[httpx.Request]) -> None:
    import hishel

    client = hishel.AsyncCacheClient(
        transport=_cached_handler(call_counter),
        storage=hishel.AsyncInMemoryStorage(),
        controller=hishel.Controller(cacheable_methods=["GET"], cacheable_status_codes=[200]),
    )
    service = HttpService(client=client, min_interval_seconds=0.0)

    body = await service.get_json("https://example.test/songdb")

    assert body == {"hits": 1}
    await service.aclose()


async def test_second_call_within_cache_window_does_not_hit_upstream_again(
    call_counter: list[httpx.Request],
) -> None:
    import hishel

    client = hishel.AsyncCacheClient(
        transport=_cached_handler(call_counter),
        storage=hishel.AsyncInMemoryStorage(),
        controller=hishel.Controller(cacheable_methods=["GET"], cacheable_status_codes=[200]),
    )
    service = HttpService(client=client, min_interval_seconds=0.0)

    first = await service.get_json("https://example.test/songdb")
    second = await service.get_json("https://example.test/songdb")

    assert first == second == {"hits": 1}
    assert len(call_counter) == 1  # 두 번째는 캐시에서 나왔다
    await service.aclose()


async def test_limiter_is_shared_per_host_not_per_url() -> None:
    import hishel

    client = hishel.AsyncCacheClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        storage=hishel.AsyncInMemoryStorage(),
    )
    service = HttpService(client=client, min_interval_seconds=5.0)

    a = service._limiter_for("https://example.test/a")
    b = service._limiter_for("https://example.test/b")
    c = service._limiter_for("https://other.test/a")

    assert a is b
    assert a is not c
    await service.aclose()


async def test_per_call_interval_uses_its_own_limiter_per_host() -> None:
    import hishel

    client = hishel.AsyncCacheClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        storage=hishel.AsyncInMemoryStorage(),
    )
    service = HttpService(client=client, min_interval_seconds=5.0)

    default = service._limiter_for("https://example.test/a")
    fast = service._limiter_for("https://example.test/a", 0.1)

    assert default is not fast
    assert fast is service._limiter_for("https://example.test/b", 0.1)
    response = await service.get("https://example.test/a", min_interval_seconds=0.0)
    assert response.status_code == 200
    await service.aclose()
