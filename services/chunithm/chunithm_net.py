# services/chunithm/chunithm_net.py
from __future__ import annotations

import http.cookiejar
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import aiosqlite
from chuni_penguin.adapters.chunithm_net.utils import is_valid_clal
from chuni_penguin.adapters.errors import AuthenticationError

from core.tool_types import ToolContext
from services.common.players import PlayersService
from services.common.secrets import SecretsService

if TYPE_CHECKING:
    from chuni_penguin.types import PersonalBest, Profile, RecentScore

logger = logging.getLogger(__name__)


class ChunithmNetAdapterLike(Protocol):
    async def get_profile(self) -> Profile: ...
    async def get_best30(self) -> list[PersonalBest]: ...
    async def get_new20(self) -> list[PersonalBest]: ...
    async def get_recent_scores(self) -> list[RecentScore]: ...
    async def logout(self) -> None: ...
    async def aclose(self) -> None: ...


AdapterFactory = Callable[[str], ChunithmNetAdapterLike]


# clal은 CHUNITHM-NET 본 사이트(chunithm-net-eng.com)가 아니라 SEGA 공통 인증
# 게이트웨이의 쿠키다 — ChunithmNetAuth(_hooks.py)가 세션이 없을 때
# lng-tgk-aime-gw.am-all.net/common_auth/login으로 가고, 거기에 clal이 실려 가야
# 자동 로그인된다. domain/path/expires는 원본 chuni-penguin의
# cogs/chunithm/auth.py(6c92675)가 만드는 쿠키와 똑같이 맞춘다.
_COOKIE_DOMAIN = "lng-tgk-aime-gw.am-all.net"
_COOKIE_PATH = "/common_auth"
_FAR_FUTURE_EXPIRES = 3_856_586_927  # 2092-03-17, 원본과 같은 값(사실상 무기한)


def _build_lwp_cookie_jar(clal: str) -> str:
    """clal 평문 하나로 올바른 LWP 쿠키 jar 문자열을 만든다. domain/path/expires가
    없으면 벤더링된 어댑터의 LWPCookieJar._really_load가 LoadError를 던지는데, 그
    예외 메시지에 평문 쿠키 값이 그대로 들어간다(CLAUDE.md 비밀 값 규칙 위반) —
    http.cookiejar.Cookie로 직접 올바른 형식을 만들어 그 경로를 피한다.
    형식이 틀린 값은 오류 메시지에 값을 넣지 않고 거절한다."""
    if not is_valid_clal(clal):
        raise ValueError("clal 형식이 올바르지 않다")
    clal = clal.removeprefix("clal=")
    jar = http.cookiejar.LWPCookieJar()
    cookie = http.cookiejar.Cookie(
        version=0, name="clal", value=clal,
        port=None, port_specified=False,
        domain=_COOKIE_DOMAIN, domain_specified=True, domain_initial_dot=False,
        path=_COOKIE_PATH, path_specified=True,
        secure=False, expires=_FAR_FUTURE_EXPIRES, discard=False,
        comment=None, comment_url=None, rest={}, rfc2109=False,
    )
    jar.set_cookie(cookie)
    return "#LWP-Cookies-2.0\n" + jar.as_lwp_str()


def _default_adapter_factory(lwp_cookie_jar: str) -> ChunithmNetAdapterLike:
    from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter

    try:
        return ChunithmNetAdapter(database=None, discord_id=0, lwp_cookie_jar=lwp_cookie_jar)
    except Exception:  # noqa: BLE001 - 어떤 예외든 쿠키가 담긴 원 예외를 숨기고 바꿔 던진다
        # from None: 원래 예외(메시지에 쿠키 줄이 들어 있을 수 있다)를 체인에 남기지 않는다.
        raise ConnectionError("CHUNITHM-NET 어댑터를 만들 수 없다") from None


@dataclass(slots=True, kw_only=True)
class LoginStatus:
    linked: bool
    nickname: str | None = None
    last_checked_at: str | None = None


@dataclass(slots=True, kw_only=True)
class SyncFetch:
    profile: Profile
    best30: list[PersonalBest]
    new20: list[PersonalBest]
    recent_scores: list[RecentScore]


class ChunithmNetService:
    """플레이어별로 CHUNITHM-NET 어댑터를 열어 준다. 복호화는 이 서비스 안에서만
    하고, 평문 쿠키는 어댑터를 만드는 그 함수 스코프를 벗어나지 않는다."""

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        secrets: SecretsService,
        players: PlayersService,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row
        self._secrets = secrets
        self._players = players
        self._adapter_factory = adapter_factory or _default_adapter_factory

    async def login(self, player_id: str, clal: str, ctx: ToolContext) -> Profile:
        """clal 값으로 프로필을 한 번 읽어 본다. 성공해야 저장하고 태그를 붙인다 —
        실패하면 아무것도 바꾸지 않는다(설계서: 실패면 이유를 본인에게만 알린다)."""
        lwp_cookie_jar = _build_lwp_cookie_jar(clal)
        adapter = self._adapter_factory(lwp_cookie_jar)
        try:
            profile = await adapter.get_profile()
        finally:
            await adapter.aclose()

        encrypted = self._secrets.encrypt(clal)
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO chunithm_net_cookies (player_id, encrypted_cookie, linked_at, last_checked_at, nickname) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (player_id) DO UPDATE SET "
            "encrypted_cookie = excluded.encrypted_cookie, linked_at = excluded.linked_at, "
            "last_checked_at = excluded.last_checked_at, nickname = excluded.nickname",
            (player_id, encrypted, now, now, profile.username),
        )
        await self._conn.commit()
        await self._players.set_status_tag(player_id, "chunithm-linked", source="chunithm_net:login")
        return profile

    async def status(self, player_id: str) -> LoginStatus:
        """상태 조회는 DB만 읽는 가벼운 호출이어야 한다 — 네트워크를 타지 않는다.
        닉네임은 login()이 저장해 둔 값을 그대로 돌려준다."""
        row = await (
            await self._conn.execute(
                "SELECT last_checked_at, nickname FROM chunithm_net_cookies WHERE player_id = ?",
                (player_id,),
            )
        ).fetchone()
        if row is None:
            return LoginStatus(linked=False)
        tags = await self._players.tags_for(player_id)
        if "chunithm-linked" not in tags:
            return LoginStatus(linked=False)
        return LoginStatus(
            linked=True, nickname=row["nickname"], last_checked_at=row["last_checked_at"]
        )

    async def logout(self, player_id: str, ctx: ToolContext) -> None:
        cookie = await self._decrypted_cookie(player_id)
        if cookie is not None:
            adapter = self._adapter_factory(cookie)
            try:
                await adapter.logout()
            except Exception:
                # 원격 로그아웃이 실패해도(만료, 점검, 네트워크) 로컬 쿠키와 태그는
                # 반드시 지운다 — 아니면 깨지거나 새어 나간 쿠키를 끊을 방법이 없다.
                logger.warning("원격 로그아웃 실패, 로컬 쿠키는 그래도 지운다", exc_info=True)
            finally:
                await adapter.aclose()
        await self._conn.execute(
            "DELETE FROM chunithm_net_cookies WHERE player_id = ?", (player_id,)
        )
        await self._conn.commit()
        await self._players.revoke_status_tag(player_id, "chunithm-linked")

    async def fetch_for_sync(self, player_id: str, *, ctx: ToolContext | None = None) -> SyncFetch:
        """동기화에 필요한 세 가지(프로필, 베스트30, 신곡20, 최근 이력)를 한 번에
        가져온다. 인증 실패(AuthenticationError)일 때만 만료로 보고
        chunithm-linked를 떼고 chunithm.login.expired를 낸 뒤 그대로 예외를 다시
        던진다 — 호출한 쪽(5단계 chunithm-sync 스킬)이 "/로그인 안내" 오류로 바꾼다.
        점검(MaintenanceError), 네트워크 오류, 파서 오류는 로그인과 무관하므로 태그를
        건드리지 않고 그대로 올려 보낸다."""
        cookie = await self._decrypted_cookie(player_id)
        if cookie is None:
            raise ConnectionError("로그인되어 있지 않다")

        adapter = self._adapter_factory(cookie)
        try:
            profile = await adapter.get_profile()
            best30 = await adapter.get_best30()
            new20 = await adapter.get_new20()
            recent_scores = await adapter.get_recent_scores()
        except AuthenticationError:
            await self._players.revoke_status_tag(player_id, "chunithm-linked")
            if ctx is not None:
                await ctx.emit("chunithm.login.expired", {"player_id": player_id})
            raise
        finally:
            await adapter.aclose()

        return SyncFetch(profile=profile, best30=best30, new20=new20, recent_scores=recent_scores)

    async def _decrypted_cookie(self, player_id: str) -> str | None:
        """평문은 이 메서드를 부른 호출자 스코프에만 잠깐 존재한다 — 반환값을
        로그나 LLM 입력에 넣지 않는 것은 호출하는 쪽의 책임이다(CLAUDE.md)."""
        row = await (
            await self._conn.execute(
                "SELECT encrypted_cookie FROM chunithm_net_cookies WHERE player_id = ?", (player_id,)
            )
        ).fetchone()
        if row is None:
            return None
        decrypted = self._secrets.decrypt(row["encrypted_cookie"])
        return _build_lwp_cookie_jar(decrypted)
