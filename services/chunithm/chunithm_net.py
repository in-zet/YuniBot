# services/chunithm/chunithm_net.py
from __future__ import annotations

import functools
import http.cookiejar
import inspect
import io
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import aiosqlite
from chuni_penguin.adapters.chunithm_net.errors import ChuniNetError
from chuni_penguin.adapters.chunithm_net.utils import is_valid_clal
from chuni_penguin.adapters.errors import AuthenticationError
from chuni_penguin.types import Difficulty

from core.tool_types import ToolContext, UserFacingError
from services.chunithm.arcade_settings import (
    FIELD_LABELS,
    ArcadeSettings,
    build_update_payload,
    named_from_settings,
    parse_arcade_settings,
)
from services.common.players import PlayersService
from services.common.secrets import SecretsService

if TYPE_CHECKING:
    from chuni_penguin.types import PersonalBest, Profile, RecentScore
    from selectolax.lexbor import LexborHTMLParser

logger = logging.getLogger(__name__)

# fetch_for_sync가 전체 PB를 모을 때 도는 순서. CHUNITHM-NET이 쓰는 코드 그대로다
# (Difficulty(code)가 "BAS"/"ADV"/"EXP"/"MAS"/"ULT"/"WE"를 그대로 알아듣는다).
_PERSONAL_BEST_DIFFICULTY_CODES: tuple[str, ...] = ("BAS", "ADV", "EXP", "MAS", "ULT", "WE")


class ChangeNotAppliedError(Exception):
    """CHUNITHM-NET에 바꾸라고 보냈는데 다시 읽어 보니 반영되지 않았다. 메시지는 사용자에게 그대로 보여 줄 수 있다."""


class ChunithmNetBlockedError(UserFacingError):
    """CHUNITHM-NET이 오류 코드로 요청을 막았다(로그인 만료·점검이 아닌 경우). 메시지는 사용자에게 그대로 보여 줄 수 있다."""


# CHUNITHM-NET 오류 코드별 안내. 모르는 코드는 코드 번호와 함께 일반 문장으로 안내한다.
_NET_ERROR_MESSAGES: dict[int, str] = {
    200010: (
        "CHUNITHM-NET이 지금은 데이터를 못 보게 막았어. 마지막 플레이가 정상적으로 끝나지 않았다는 뜻이라, "
        "게임을 결과 화면까지 끝내고 조금 뒤에 다시 해 줘."
    ),
    ChuniNetError.RATE_LIMIT_EXCEEDED: "CHUNITHM-NET이 요청이 너무 잦다고 막았어. 잠시 뒤에 다시 해 줘.",
    ChuniNetError.PROFILE_BANNED: "이 계정은 CHUNITHM-NET에서 이용이 제한돼 있어서 못 가져와.",
}


def _blocked_error(exc: ChuniNetError) -> ChunithmNetBlockedError:
    message = _NET_ERROR_MESSAGES.get(exc.code) or f"CHUNITHM-NET이 오류 코드 {exc.code}로 요청을 막았어. 잠시 뒤에 다시 해 줘."
    return ChunithmNetBlockedError(message)


class _ErrorTranslatingAdapter:
    """어댑터의 비동기 메서드가 올리는 `ChuniNetError`(사이트의 오류 페이지)를 사용자에게 보여 줄 수 있는
    `ChunithmNetBlockedError`로 바꿔 던진다. 그 밖의 속성은 그대로 넘긴다. 로그인 만료(200002·200004)는
    어댑터가 다시 로그인해 처리하므로 여기까지 오지 않는다."""

    def __init__(self, inner: ChunithmNetAdapterLike) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not inspect.iscoroutinefunction(attr):
            return attr

        @functools.wraps(attr)
        async def call(*args: Any, **kwargs: Any) -> Any:
            try:
                return await attr(*args, **kwargs)
            except ChuniNetError as exc:
                raise _blocked_error(exc) from exc

        return call


class ChunithmNetAdapterLike(Protocol):
    async def get_profile(self) -> Profile: ...
    async def get_best30(self) -> list[PersonalBest]: ...
    async def get_new20(self) -> list[PersonalBest]: ...
    async def get_recent_scores(self) -> list[RecentScore]: ...
    async def get_personal_bests(self, *, difficulty: Difficulty) -> list[PersonalBest]: ...
    async def get_detailed_recent_score(self, score: RecentScore) -> RecentScore: ...
    async def get_favorite_music(self) -> list[int]: ...
    async def set_favorite_music(self, ids: Sequence[int]) -> None: ...
    async def logout(self) -> None: ...
    async def aclose(self) -> None: ...

    # chunithm-arcade·chunithm-settings 전용: 설정 페이지(get_profile 등과 달리
    # chuni-penguin에 메서드가 없다)를 읽고 쓰려고 어댑터의 공용 요청 메서드와 CSRF
    # 역할의 `_t` 쿠키를 그대로 쓴다. 이름이 private(밑줄)인 건 third_party 내부
    # 구현이라서다 — 쿠키·인증·에러 훅까지 다시 구현하는 중복을 피하려고 이 Protocol에
    # 얹었다(docs/design/skills/chunithm-arcade.md 구현 결정).
    async def _request_as_soup(self, method: str, url: str, **kwargs: object) -> LexborHTMLParser: ...

    @property
    def _token(self) -> str | None: ...

    @property
    def lwp_cookie_jar(self) -> str: ...


AdapterFactory = Callable[[str], ChunithmNetAdapterLike]
# ID/비번으로 새로 로그인할 때 쓴다(기존 쿠키가 없으므로 jar 대신 username/password를
# 받는다). 벤더링된 ChunithmNetAdapter가 이미 이 둘을 받아 SEGA 공통 인증
# 게이트웨이와 sid+password로 로그인하는 흐름(_hooks.py의 ChunithmNetAuth)을 그대로
# 구현해 둬서, 우리는 빈 쿠키 jar로 어댑터를 만들고 그 흐름에 맡기기만 하면 된다.
CredentialAdapterFactory = Callable[[str, str], ChunithmNetAdapterLike]


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
        adapter = ChunithmNetAdapter(database=None, discord_id=0, lwp_cookie_jar=lwp_cookie_jar)
    except Exception:  # noqa: BLE001 - 어떤 예외든 쿠키가 담긴 원 예외를 숨기고 바꿔 던진다
        # from None: 원래 예외(메시지에 쿠키 줄이 들어 있을 수 있다)를 체인에 남기지 않는다.
        raise ConnectionError("CHUNITHM-NET 어댑터를 만들 수 없다") from None
    return _ErrorTranslatingAdapter(adapter)  # pyright: ignore[reportReturnType]


# 쿠키가 하나도 없는 빈 jar. ID/비번으로 처음 로그인할 때 이 jar로 어댑터를 만들면,
# 첫 요청이 바로 미인증으로 걸려 ChunithmNetAuth가 sid+password 로그인을 스스로
# 시도한다 — 우리가 로그인 절차 자체를 다시 구현하지 않는다.
_EMPTY_LWP_JAR = "#LWP-Cookies-2.0\n"


def _default_credential_adapter_factory(username: str, password: str) -> ChunithmNetAdapterLike:
    from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter

    try:
        adapter = ChunithmNetAdapter(
            database=None, discord_id=0, lwp_cookie_jar=_EMPTY_LWP_JAR, username=username, password=password
        )
    except Exception:  # noqa: BLE001 - username/password가 예외 메시지에 섞여 나가지 않게 한다
        raise ConnectionError("CHUNITHM-NET 어댑터를 만들 수 없다") from None
    return _ErrorTranslatingAdapter(adapter)  # pyright: ignore[reportReturnType]


def _extract_clal(lwp_cookie_jar: str) -> str | None:
    """로그인 성공 뒤 어댑터가 돌려주는 전체 쿠키 jar 문자열에서 clal 값만 뽑는다.
    나머지(세션 쿠키 등)는 그대로 버린다 — 우리는 clal 하나만 저장하고, 필요할 때마다
    _build_lwp_cookie_jar로 다시 jar를 만든다(login()이 이미 그렇게 한다)."""
    jar = http.cookiejar.LWPCookieJar()
    jar._really_load(  # pyright: ignore[reportAttributeAccessIssue]
        io.StringIO(lwp_cookie_jar), "?", ignore_discard=False, ignore_expires=False
    )
    for cookie in jar:
        if cookie.name == "clal" and cookie.domain == _COOKIE_DOMAIN:
            return cookie.value
    return None


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
    # 키는 "BAS"/"ADV"/"EXP"/"MAS"/"ULT"/"WE" (_PERSONAL_BEST_DIFFICULTY_CODES).
    all_personal_bests: dict[str, list[PersonalBest]] = field(default_factory=dict)


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
        credential_adapter_factory: CredentialAdapterFactory | None = None,
    ) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row
        self._secrets = secrets
        self._players = players
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._credential_adapter_factory = credential_adapter_factory or _default_credential_adapter_factory

    async def login(self, player_id: str, clal: str, ctx: ToolContext) -> Profile:
        """clal 값으로 프로필을 한 번 읽어 본다. 성공해야 저장하고 태그를 붙인다 —
        실패하면 아무것도 바꾸지 않는다(설계서: 실패면 이유를 본인에게만 알린다)."""
        lwp_cookie_jar = _build_lwp_cookie_jar(clal)
        adapter = self._adapter_factory(lwp_cookie_jar)
        try:
            profile = await adapter.get_profile()
        finally:
            await adapter.aclose()

        await self._store_clal(player_id, clal, profile.username)
        return profile

    async def login_with_credentials(
        self, player_id: str, username: str, password: str, ctx: ToolContext
    ) -> Profile:
        """SEGA ID/비번으로 한 번만 로그인해서 clal을 뽑아 저장한다. username/password는
        이 함수 스코프를 벗어나지 않는다 — 저장하지도, 로그에 남기지도 않는다. 성공해야
        저장하고 태그를 붙인다(login()과 같은 규칙). 로그인에 성공했는데 clal을 못 찾으면
        (벤더링된 어댑터의 쿠키 모양이 바뀌었다는 뜻) 아무것도 저장하지 않고 ConnectionError를
        던진다."""
        adapter = self._credential_adapter_factory(username, password)
        try:
            profile = await adapter.get_profile()
            clal = _extract_clal(adapter.lwp_cookie_jar)
        finally:
            await adapter.aclose()

        if clal is None:
            raise ConnectionError("로그인에 성공했지만 clal을 찾지 못했다")

        await self._store_clal(player_id, clal, profile.username)
        return profile

    async def _store_clal(self, player_id: str, clal: str, nickname: str) -> None:
        """login()과 login_with_credentials()가 공유하는 저장 로직 — 암호화해 넣고
        chunithm-linked 태그를 붙인다."""
        encrypted = self._secrets.encrypt(clal)
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO chunithm_net_cookies (player_id, encrypted_cookie, linked_at, last_checked_at, nickname) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (player_id) DO UPDATE SET "
            "encrypted_cookie = excluded.encrypted_cookie, linked_at = excluded.linked_at, "
            "last_checked_at = excluded.last_checked_at, nickname = excluded.nickname",
            (player_id, encrypted, now, now, nickname),
        )
        await self._conn.commit()
        await self._players.set_status_tag(player_id, "chunithm-linked", source="chunithm_net:login")

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
        try:
            cookie = await self._decrypted_cookie(player_id)
        except Exception:
            # 복호화가 안 되는 쿠키(키 교체 등)도 로컬에서는 끊을 수 있어야 한다.
            logger.warning("저장된 쿠키를 읽지 못해 원격 로그아웃은 건너뛴다", exc_info=True)
            cookie = None
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
        """동기화에 필요한 것(프로필, 베스트30, 신곡20, 최근 이력, 난이도별 전체 PB)을
        한 번에 가져온다. 인증 실패(AuthenticationError)일 때만 만료로 보고
        chunithm-linked를 떼고 chunithm.login.expired를 낸 뒤 그대로 예외를 다시
        던진다 — 호출한 쪽(chunithm-sync 스킬)이 "/로그인 안내" 오류로 바꾼다.
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
            all_personal_bests: dict[str, list[PersonalBest]] = {}
            for code in _PERSONAL_BEST_DIFFICULTY_CODES:
                all_personal_bests[code] = await adapter.get_personal_bests(difficulty=Difficulty(code))
        except AuthenticationError:
            await self._expire_login(player_id, ctx)
            raise
        finally:
            await adapter.aclose()

        return SyncFetch(
            profile=profile,
            best30=best30,
            new20=new20,
            recent_scores=recent_scores,
            all_personal_bests=all_personal_bests,
        )

    async def get_recent_scores(
        self, player_id: str, *, ctx: ToolContext | None = None
    ) -> list[RecentScore]:
        """최근 플레이 이력만 따로 가져온다(동기화와 달리 판정 상세 없이 목록만).
        인증 실패는 fetch_for_sync와 똑같이 만료로 처리하고 예외를 다시 던진다."""
        cookie = await self._decrypted_cookie(player_id)
        if cookie is None:
            raise ConnectionError("로그인되어 있지 않다")

        adapter = self._adapter_factory(cookie)
        try:
            return await adapter.get_recent_scores()
        except AuthenticationError:
            await self._expire_login(player_id, ctx)
            raise
        finally:
            await adapter.aclose()

    async def get_arcade_settings(
        self, player_id: str, *, ctx: ToolContext | None = None
    ) -> ArcadeSettings:
        """지금 계정의 게임 설정 페이지를 읽는다(`chunithm-arcade` 전용). 인증 실패는
        다른 메서드와 똑같이 만료로 처리하고 예외를 다시 던진다."""
        cookie = await self._decrypted_cookie(player_id)
        if cookie is None:
            raise ConnectionError("로그인되어 있지 않다")

        adapter = self._adapter_factory(cookie)
        try:
            soup = await adapter._request_as_soup("GET", "/mobile/home/userOption/updateUserOption")
            return parse_arcade_settings(soup)
        except AuthenticationError:
            await self._expire_login(player_id, ctx)
            raise
        finally:
            await adapter.aclose()

    async def update_arcade_settings(
        self, player_id: str, *, changes: dict[str, str], ctx: ToolContext | None = None
    ) -> ArcadeSettings:
        """게임 설정 중 `changes`에 담긴 항목만 바꾸고 나머지는 지금 값 그대로 다시
        제출한다(`chunithm-settings` 전용). POST 응답을 믿지 않고 다시 읽어 실제로
        반영된 값을 돌려주며, 요청한 값과 다르면 `ChangeNotAppliedError`다. `changes`의 표시 텍스트가 선택지에 없으면
        `build_update_payload`의 ValueError가 그대로 올라간다(모델에 보여줄 한글 메시지)."""
        cookie = await self._decrypted_cookie(player_id)
        if cookie is None:
            raise ConnectionError("로그인되어 있지 않다")

        adapter = self._adapter_factory(cookie)
        try:
            soup = await adapter._request_as_soup("GET", "/mobile/home/userOption/updateUserOption")
            payload = build_update_payload(soup, changes)
            payload["token"] = adapter._token or ""
            await adapter._request_as_soup(
                "POST", "/mobile/home/userOption/updateUserOption/update/", data=payload,
            )
            soup = await adapter._request_as_soup("GET", "/mobile/home/userOption/updateUserOption")
            settings = parse_arcade_settings(soup)
            not_applied = [
                f"{FIELD_LABELS[attr]}: 요청 {requested}, 지금 {named_from_settings(settings)[attr]}"
                for attr, requested in changes.items()
                if named_from_settings(settings)[attr] != requested
            ]
            if not_applied:
                raise ChangeNotAppliedError("CHUNITHM-NET에 반영되지 않았어 — " + "; ".join(not_applied))
            return settings
        except AuthenticationError:
            await self._expire_login(player_id, ctx)
            raise
        finally:
            await adapter.aclose()

    async def get_favorite_music(self, player_id: str, *, ctx: ToolContext | None = None) -> list[int]:
        """지금 즐겨찾기 곡 id 목록을 읽는다(`chunithm-settings` 전용)."""
        cookie = await self._decrypted_cookie(player_id)
        if cookie is None:
            raise ConnectionError("로그인되어 있지 않다")

        adapter = self._adapter_factory(cookie)
        try:
            return await adapter.get_favorite_music()
        except AuthenticationError:
            await self._expire_login(player_id, ctx)
            raise
        finally:
            await adapter.aclose()

    async def set_favorite_music(
        self, player_id: str, ids: Sequence[int], *, ctx: ToolContext | None = None
    ) -> None:
        """즐겨찾기 곡 id 목록을 통째로 교체한다(`chunithm-settings` 전용) — 호출하는
        쪽이 추가·삭제를 반영한 전체 목록을 만들어 넘긴다."""
        cookie = await self._decrypted_cookie(player_id)
        if cookie is None:
            raise ConnectionError("로그인되어 있지 않다")

        adapter = self._adapter_factory(cookie)
        try:
            # 요청 토큰(`_t` 쿠키)은 로그인 뒤에야 생긴다. 새 어댑터로 바로 POST하면 비어 있거나 옛 토큰이 body에
            # 실리고, 사이트가 거절해 재로그인한 뒤에도 같은 body가 다시 나가 조용히 반영이 안 된다(2026-10-21
            # 실기: 즐겨찾기 추가·삭제가 하나도 안 먹었다). 먼저 한 번 읽어 세션과 토큰을 만든 뒤 보낸다.
            await adapter.get_favorite_music()
            await adapter.set_favorite_music(ids)
            after = await adapter.get_favorite_music()
            if set(after) != set(ids):
                raise ChangeNotAppliedError(
                    f"CHUNITHM-NET에 반영되지 않았어 — 보낸 곡 {len(set(ids))}개, 지금 즐겨찾기 {len(set(after))}개"
                )
        except AuthenticationError:
            await self._expire_login(player_id, ctx)
            raise
        finally:
            await adapter.aclose()

    async def enrich_recent_with_detail(
        self, player_id: str, scores: list[RecentScore], *, limit: int, ctx: ToolContext | None = None
    ) -> list[RecentScore]:
        """앞 `limit`개만 상세 판정이 붙은 버전으로 바꿔 끼운 새 리스트를 돌려준다.
        나머지는 받은 그대로 둔다. 넘겨받은 `scores` 리스트 자체는 건드리지 않는다.
        인증 실패는 fetch_for_sync와 똑같이 만료로 처리하고 예외를 다시 던진다."""
        cookie = await self._decrypted_cookie(player_id)
        if cookie is None:
            raise ConnectionError("로그인되어 있지 않다")

        adapter = self._adapter_factory(cookie)
        try:
            detailed = [await adapter.get_detailed_recent_score(score) for score in scores[:limit]]
        except AuthenticationError:
            await self._expire_login(player_id, ctx)
            raise
        finally:
            await adapter.aclose()

        return detailed + scores[limit:]

    async def _expire_login(self, player_id: str, ctx: ToolContext | None) -> None:
        """인증 실패(쿠키 만료) 처리: chunithm-linked를 떼고 chunithm.login.expired를 낸다.
        이벤트의 대상은 쿠키 주인(player_id)이다 — /최근처럼 요청한 사람과 쿠키 주인이 다를
        수 있으므로 ctx.emit(요청한 사람 기준) 대신 버스에 대상 플레이어를 직접 넘긴다."""
        await self._players.revoke_status_tag(player_id, "chunithm-linked")
        if ctx is not None:
            await ctx.event_bus.emit_notify(
                "chunithm.login.expired", {"player_id": player_id},
                player_id=player_id, guild_id=ctx.guild_id,
            )

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
