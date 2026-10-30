# services/common/aime_auth.py
"""SEGA 공통 인증 게이트웨이(aime) 로그인 흐름. 게임마다 `AimeSite`(site_id·돌아올 주소)만 다르다.

흐름은 chuni-penguin의 `ChunithmNetAuth`(third_party)와 같다: 요청이 로그인 풀림으로 돌아오면 게이트웨이
로그인 주소로 간다 → `clal` 쿠키가 있으면 거기서 곧바로 사이트로 돌아오고, 아니면 sid/비밀번호로 로그인한다 →
액세스 코드(aime 카드)가 없는 계정을 알아차린다 → 새 쿠키로 원래 요청을 다시 보낸다. `third_party/`는 고치지
않으므로 새로 쓴다. CHUNITHM 쪽은 아직 기존 경로를 그대로 쓴다(바꿀지는 나중에 따로 정한다)."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Generator
from dataclasses import dataclass

import httpx
from chuni_penguin.adapters.errors import AuthenticationError, NoCardsRegistered
from selectolax.lexbor import LexborHTMLParser

GATEWAY_HOST = "lng-tgk-aime-gw.am-all.net"
_LOGIN_PATH = "/common_auth/login"
_LOGIN_SID_URL = f"https://{GATEWAY_HOST}/common_auth/login/sid"
_REDIRECT_PATH = "/common_auth/redirect"
_ADD_ACCESS_CODE_URL = "https://common-access.am-all.net/access/code/add"


@dataclass(slots=True, kw_only=True, frozen=True)
class AimeSite:
    site_id: str
    redirect_url: str
    back_url: str

    @property
    def login_url(self) -> httpx.URL:
        return httpx.URL(
            f"https://{GATEWAY_HOST}{_LOGIN_PATH}",
            params={"site_id": self.site_id, "redirect_url": self.redirect_url, "back_url": self.back_url},
        )


class AimeAuth(httpx.Auth):
    """`needs_login(response)`가 True인 응답(로그인이 풀린 사이트의 답)을 받으면 다시 로그인한다.
    리다이렉트를 따라가는 클라이언트에 써야 한다."""

    requires_request_body = True
    requires_response_body = True

    def __init__(
        self,
        client: httpx.AsyncClient,
        site: AimeSite,
        *,
        needs_login: Callable[[httpx.Response], bool],
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.client = client
        self._site = site
        self._needs_login = needs_login
        self._username = username
        self._password = password

    def _is_login_page(self, response: httpx.Response) -> bool:
        login_url = self._site.login_url
        return response.url.host == login_url.host and response.url.path == login_url.path

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        response = yield request
        if not self._needs_login(response):
            return

        auth_response = yield self.client.build_request("GET", self._site.login_url)

        if self._is_login_page(auth_response):
            if self._username is None or self._password is None:
                raise AuthenticationError("저장된 로그인 정보가 더는 유효하지 않다")
            auth_response = yield self.client.build_request(
                "POST", _LOGIN_SID_URL,
                data={"retention": "1", "sid": self._username, "password": self._password},
            )
            if self._is_login_page(auth_response):
                raise AuthenticationError("ID나 비밀번호가 틀렸거나 2단계 인증이 켜진 계정이다")
            if auth_response.url.host == GATEWAY_HOST and auth_response.url.path != _REDIRECT_PATH:
                raise AuthenticationError("알 수 없는 이유로 로그인하지 못했다")

        if auth_response.url.host == GATEWAY_HOST and auth_response.url.path == _REDIRECT_PATH:
            form = LexborHTMLParser(auth_response.content).css_first("form")
            if form is not None and form.attributes.get("action") == _ADD_ACCESS_CODE_URL:
                raise NoCardsRegistered(
                    "이 계정에는 등록된 액세스 코드가 없다. https://my-aime.net 에서 먼저 등록해야 한다"
                )

        if str(auth_response.url) == str(request.url):
            return

        # 새 세션 쿠키로 원래 요청을 다시 보낸다. 그래도 풀려 있으면 부른 쪽이 응답을 보고 알아차린다.
        with contextlib.suppress(KeyError):
            del request.headers["cookie"]
            del request.headers["cookie2"]
        self.client.cookies.set_cookie_header(request)
        yield request
