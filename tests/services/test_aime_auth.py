# tests/services/test_aime_auth.py
from __future__ import annotations

import httpx
import pytest
from chuni_penguin.adapters.errors import AuthenticationError, NoCardsRegistered

from services.common.aime_auth import GATEWAY_HOST, AimeAuth, AimeSite

SITE = AimeSite(
    site_id="testsite", redirect_url="https://site.example/mobile/", back_url="https://site.example/"
)
_NO_CARD_PAGE = '<html><body><form action="https://common-access.am-all.net/access/code/add"></form></body></html>'


def _lost(response: httpx.Response) -> bool:
    return response.url.path == "/mobile/error/" or response.url.host == GATEWAY_HOST


class _Site:
    """사이트와 게이트웨이를 흉내 내는 서버. 세션 쿠키가 있어야 페이지를 준다."""

    def __init__(self, *, cards: bool = True, sid_ok: bool = True) -> None:
        self.cards = cards
        self.sid_ok = sid_ok
        self.calls: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.host}{request.url.path}")
        cookies = request.headers.get("cookie", "")
        if request.url.host == "site.example":
            if request.url.path == "/mobile/error/":
                return httpx.Response(200, text="error")
            if request.url.path == "/mobile/":
                return httpx.Response(200, text="home", headers={"set-cookie": "sess=1; Path=/"})
            if "sess=1" not in cookies:
                return httpx.Response(302, headers={"location": "https://site.example/mobile/error/"})
            return httpx.Response(200, text=f"page {request.url.path}")
        if request.url.path == "/common_auth/login":
            if "clal=good" in cookies:
                return self._into_site()
            return httpx.Response(200, text="login form")
        if request.url.path == "/common_auth/login/sid":
            if not self.sid_ok:
                return httpx.Response(302, headers={"location": f"https://{GATEWAY_HOST}/common_auth/login"})
            return self._into_site()
        if request.url.path == "/common_auth/redirect":
            return httpx.Response(200, text=_NO_CARD_PAGE)
        raise AssertionError(f"예상하지 못한 요청 {request.url}")

    def _into_site(self) -> httpx.Response:
        if not self.cards:
            return httpx.Response(302, headers={"location": f"https://{GATEWAY_HOST}/common_auth/redirect"})
        return httpx.Response(302, headers={"location": "https://site.example/mobile/"})


def _client(site: _Site, *, clal: str | None, username: str | None = None, password: str | None = None) -> httpx.AsyncClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handle), follow_redirects=True)
    if clal is not None:
        client.cookies.set("clal", clal, domain=GATEWAY_HOST, path="/common_auth")
    client.auth = AimeAuth(client, SITE, needs_login=_lost, username=username, password=password)
    return client


async def test_logged_in_session_makes_a_single_request() -> None:
    site = _Site()
    async with _client(site, clal="good") as client:
        client.cookies.set("sess", "1", domain="site.example", path="/")
        response = await client.get("https://site.example/mobile/profile")

    assert response.text == "page /mobile/profile"
    assert len(site.calls) == 1


async def test_clal_logs_in_again_and_retries_the_original_request() -> None:
    site = _Site()
    async with _client(site, clal="good") as client:
        response = await client.get("https://site.example/mobile/profile")

    assert response.text == "page /mobile/profile"
    assert f"GET {GATEWAY_HOST}/common_auth/login" in site.calls
    assert not any("login/sid" in call for call in site.calls)


async def test_expired_clal_without_credentials_is_an_authentication_error() -> None:
    async with _client(_Site(), clal="stale") as client:
        with pytest.raises(AuthenticationError):
            await client.get("https://site.example/mobile/profile")


async def test_credentials_log_in_when_clal_is_missing() -> None:
    site = _Site()
    async with _client(site, clal=None, username="id", password="pw") as client:
        response = await client.get("https://site.example/mobile/profile")

    assert response.text == "page /mobile/profile"
    assert f"POST {GATEWAY_HOST}/common_auth/login/sid" in site.calls


async def test_wrong_credentials_are_an_authentication_error() -> None:
    async with _client(_Site(sid_ok=False), clal=None, username="id", password="bad") as client:
        with pytest.raises(AuthenticationError, match="틀렸"):
            await client.get("https://site.example/mobile/profile")


async def test_account_without_access_code_is_reported() -> None:
    async with _client(_Site(cards=False), clal="good") as client:
        with pytest.raises(NoCardsRegistered):
            await client.get("https://site.example/mobile/profile")
