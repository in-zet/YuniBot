# services/maimai/maimai_net.py
"""maimai DX NET(국제판) 어댑터와 동기화용 서비스.

로그인은 CHUNITHM과 같은 SEGA 공통 인증(`clal`)을 쓴다 — 쿠키는 `sega_cookies` 하나에 있다. 로그인은 보통
CHUNITHM 서비스로 하지만, 요청·선호 게임이 maimai이거나 CHUNITHM-NET이 점검이면 여기서도 한다(2026-11-22). 복호화는 `SegaCookieStore`를 통해 이 서비스 안에서만
하고, 평문 `clal`은 어댑터를 만드는 스코프를 벗어나지 않는다."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote

import aiolimiter
import httpx
from chuni_penguin.adapters._hooks import raise_on_server_errors
from chuni_penguin.adapters.errors import AuthenticationError
from selectolax.lexbor import LexborHTMLParser

from core.tool_types import ToolContext, UserFacingError
from services.common.aime_auth import GATEWAY_HOST, AimeAuth, AimeSite
from services.common.sega_cookies import LoginStatus, SegaCookieStore
from services.game.net_settings import (
    ChangeNotAppliedError,
    GameSettings,
    build_update_payload,
    parse_settings,
    raise_if_not_applied,
)
from services.game.play_counts import PlayActivity, RecentPlay
from services.maimai import parser
from services.maimai.parser import MaimaiProfile, PlayEntry, RatingTargets, ScoreEntry
from services.maimai.sync_snapshot import snapshot_from_pages
from services.maimai.user_option import (
    SETTINGS_FIELDS,
    favorite_payload,
    form_token,
    parse_favorite_page,
)

if TYPE_CHECKING:
    import aiosqlite

    from services.common.players import PlayersService
    from services.common.secrets import SecretsService
    from services.game.sync_types import SyncSnapshot

logger = logging.getLogger(__name__)


_BASE_URL = "https://maimaidx-eng.com"
_MOBILE = "/maimai-mobile"
_ERROR_PATH = f"{_MOBILE}/error/"
_OPTION_PATH = f"{_MOBILE}/home/userOption/updateUserOption/"
_FAVORITE_PATH = f"{_MOBILE}/home/userOption/favorite/updateMusic"
SITE = AimeSite(
    site_id="maimaidxex", redirect_url=f"{_BASE_URL}{_MOBILE}/", back_url="https://maimai.sega.com/"
)
_CLAL_DOMAIN = GATEWAY_HOST
_CLAL_PATH = "/common_auth"

# 로그인이 풀렸다는 오류 코드: 100001("Please login again."), 200002(연결 만료), 200004(세션 무효). CHUNITHM-NET과 같다.
SESSION_LOST_CODES = frozenset({100001, 200002, 200004})
# 난이도별 기록 목록의 `diff` 값. 宴은 10이다.
_LIST_DIFF = {"BAS": 0, "ADV": 1, "EXP": 2, "MAS": 3, "REM": 4, "UTA": 10}
LIST_DIFFICULTIES: tuple[str, ...] = tuple(_LIST_DIFF)
# 플레이 횟수(곡마다 상세 하나)를 받을 때의 초당 요청 수(2026-11-28 사용자 결정 — 동기화 뒤 배경 수집).
PLAY_COUNT_RATE = 5

_NET_ERROR_MESSAGES: dict[int, str] = {}  # 알게 되는 대로 더한다. 모르는 코드는 번호와 함께 일반 문장으로 안내한다.


class MaimaiNetError(UserFacingError):
    """maimai DX NET이 오류 페이지로 요청을 막았다(로그인 풀림·점검이 아닌 경우). 메시지는 사용자에게 그대로 보여 줄 수 있다."""

    def __init__(self, code: int) -> None:
        message = _NET_ERROR_MESSAGES.get(code) or f"maimai DX NET이 오류 코드 {code}로 요청을 막았어. 잠시 뒤에 다시 해 줘."
        super().__init__(message)
        self.code = code


def _is_error_page(response: httpx.Response) -> bool:
    return response.url.host == httpx.URL(_BASE_URL).host and response.url.path == _ERROR_PATH


def _session_lost(response: httpx.Response) -> bool:
    if response.url.host == GATEWAY_HOST:
        return True
    return _is_error_page(response) and parser.error_code(response.content) in SESSION_LOST_CODES


async def _raise_on_net_error(response: httpx.Response) -> None:
    """로그인 풀림(다시 로그인하면 된다)이 아닌 오류 페이지는 사용자에게 보여 줄 오류로 바꾼다."""
    if not _is_error_page(response):
        return
    code = parser.error_code(await response.aread())
    if code is None:
        raise parser.MaimaiParseError("오류 페이지에서 오류 코드를 읽지 못했다")
    if code not in SESSION_LOST_CODES:
        raise MaimaiNetError(code)


class MaimaiNetAdapter:
    """한 번의 동기화 동안 쓰는 NET 연결. 로그인이 풀려 있으면 `clal`로 스스로 다시 로그인한다."""

    def __init__(
        self,
        clal: str | None,
        *,
        username: str | None = None,
        password: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: aiolimiter.AsyncLimiter | None = None,
    ) -> None:
        """`clal`이 없으면 username/password로 로그인한다(`/로그인`). 둘은 이 연결의 인증 객체에만 있다."""
        limiter = limiter if limiter is not None else aiolimiter.AsyncLimiter(1, 1)

        async def throttle(request: httpx.Request) -> None:
            await limiter.acquire()

        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=httpx.Timeout(60.0),
            follow_redirects=True,
            transport=transport,
            event_hooks={"request": [throttle], "response": [raise_on_server_errors, _raise_on_net_error]},
            headers={
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/114.0",
                "accept-language": "en-US,en;q=0.5",
            },
        )
        if clal is not None:
            self._client.cookies.set("clal", clal, domain=_CLAL_DOMAIN, path=_CLAL_PATH)
        self._client.auth = AimeAuth(
            self._client, SITE, needs_login=_session_lost, username=username, password=password
        )

    def session_clal(self) -> str | None:
        """로그인 뒤 이 연결이 가진 `clal`. 저장할 때만 꺼낸다."""
        for cookie in self._client.cookies.jar:
            if cookie.name == "clal" and cookie.domain.lstrip(".") == _CLAL_DOMAIN:
                return cookie.value
        return None

    async def _get(self, path: str) -> bytes:
        response = await self._client.get(path)
        if _session_lost(response):
            raise AuthenticationError("로그인했는데도 세션이 풀려 있다")
        return response.content

    async def get_rating_targets(self) -> tuple[MaimaiProfile, RatingTargets]:
        """프로필과 레이팅 대상곡(신곡 15 + 구곡 35). 같은 페이지 맨 위에 프로필이 있다."""
        html = await self._get(f"{_MOBILE}/home/ratingTargetMusic/")
        return parser.parse_profile(html), parser.parse_rating_targets(html)

    async def get_recent(self) -> list[PlayEntry]:
        return parser.parse_recent(await self._get(f"{_MOBILE}/record/"))

    async def get_music_list(self, difficulty: str) -> list[ScoreEntry]:
        """한 난이도에서 친 적 있는 곡 전부(달성률순). `difficulty`는 `LIST_DIFFICULTIES` 중 하나다."""
        path = f"{_MOBILE}/record/musicSort/search/?search=V&sort=1&playCheck=on&diff={_LIST_DIFF[difficulty]}"
        return parser.parse_music_list(await self._get(path))

    async def _post(self, path: str, data: Mapping[str, str | list[str]]) -> None:
        response = await self._client.post(path, data=data)
        if _session_lost(response):
            raise AuthenticationError("로그인했는데도 세션이 풀려 있다")

    async def get_user_option(self) -> bytes:
        """인게임 설정 페이지(폼 하나에 `<select>` 40개 남짓과 요청 토큰)."""
        return await self._get(_OPTION_PATH)

    async def post_user_option(self, payload: Mapping[str, str]) -> None:
        await self._post(f"{_OPTION_PATH}update/", payload)

    async def get_favorite_page(self) -> bytes:
        """즐겨찾기 바꾸기 페이지(수록곡 전부의 체크 칸, 지금 즐겨찾기는 체크돼 있다)."""
        return await self._get(_FAVORITE_PATH)

    async def post_favorites(self, payload: Mapping[str, str | list[str]]) -> None:
        await self._post(f"{_FAVORITE_PATH}/set", payload)

    async def get_music_index(self) -> dict[tuple[str, str], str]:
        """(제목, 채보 종류) → 곡 상세로 가는 값. 전체 장르 MAS 목록이라 안 친 곡도 들어 있다(宴은 없다)."""
        return parser.parse_music_index(await self._get(f"{_MOBILE}/record/musicGenre/search/?genre=99&diff=3"))

    async def get_total_play_count(self) -> int | None:
        """플레이어 정보의 총 플레이 횟수(크레딧)."""
        return parser.parse_total_play_count(await self._get(f"{_MOBILE}/playerData/"))

    async def get_play_counts(self, idx: str) -> dict[tuple[str, str], int]:
        """곡 상세의 (채보 종류, 난이도) → 플레이 횟수."""
        return parser.parse_play_counts(await self._get(f"{_MOBILE}/record/musicDetail/?idx={quote(idx, safe='')}"))

    async def aclose(self) -> None:
        await self._client.aclose()


class MaimaiNetAdapterLike(Protocol):
    def session_clal(self) -> str | None: ...
    async def get_rating_targets(self) -> tuple[MaimaiProfile, RatingTargets]: ...
    async def get_recent(self) -> list[PlayEntry]: ...
    async def get_music_list(self, difficulty: str) -> list[ScoreEntry]: ...
    async def get_user_option(self) -> bytes: ...
    async def post_user_option(self, payload: Mapping[str, str]) -> None: ...
    async def get_favorite_page(self) -> bytes: ...
    async def post_favorites(self, payload: Mapping[str, str | list[str]]) -> None: ...
    async def get_music_index(self) -> dict[tuple[str, str], str]: ...
    async def get_total_play_count(self) -> int | None: ...
    async def get_play_counts(self, idx: str) -> dict[tuple[str, str], int]: ...
    async def aclose(self) -> None: ...


AdapterFactory = Callable[[str], MaimaiNetAdapterLike]
CredentialAdapterFactory = Callable[[str, str], MaimaiNetAdapterLike]


def _credential_adapter(username: str, password: str) -> MaimaiNetAdapterLike:
    return MaimaiNetAdapter(None, username=username, password=password)


def _play_count_adapter(clal: str) -> MaimaiNetAdapterLike:
    """플레이 횟수 전용 연결: 곡마다 상세를 하나씩 받으므로 초당 `PLAY_COUNT_RATE`번까지(다른 요청은 초당 1번)."""
    return MaimaiNetAdapter(clal, limiter=aiolimiter.AsyncLimiter(PLAY_COUNT_RATE, 1))


@dataclass(slots=True, kw_only=True, frozen=True)
class LoginProfile:
    """로그인 결과. CHUNITHM 서비스의 프로필처럼 `username`(닉네임)을 준다."""

    username: str


class MaimaiNetService:
    """플레이어의 maimai DX 기록을 가져온다. 스킬에는 `chunithm_net` 자리로 들어가므로(게임마다 그 자리에
    그 게임의 NET 서비스가 낀다) 같은 이름의 `fetch_snapshot`을 갖는다."""

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        secrets: SecretsService,
        players: PlayersService,
        adapter_factory: AdapterFactory | None = None,
        credential_adapter_factory: CredentialAdapterFactory | None = None,
        play_count_adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self._cookies = SegaCookieStore(conn=conn, secrets=secrets, players=players)
        self._adapter_factory: AdapterFactory = adapter_factory or MaimaiNetAdapter
        self._play_count_adapter_factory: AdapterFactory = play_count_adapter_factory or _play_count_adapter
        self._credential_adapter_factory: CredentialAdapterFactory = credential_adapter_factory or _credential_adapter

    async def status(self, player_id: str) -> LoginStatus:
        return await self._cookies.status(player_id)

    async def login_with_credentials(
        self, player_id: str, username: str, password: str, ctx: ToolContext
    ) -> LoginProfile:
        """SEGA ID/비밀번호로 maimai DX NET에 한 번 로그인해 `clal`을 저장한다(게임 공통 쿠키). ID/비밀번호는 저장하지
        않는다. 프로필을 읽어야 성공으로 본다 — 점검(MaintenanceError)·인증 실패는 그대로 던진다."""
        adapter = self._credential_adapter_factory(username, password)
        try:
            profile, _targets = await adapter.get_rating_targets()
            clal = adapter.session_clal()
        finally:
            await adapter.aclose()
        if clal is None:
            raise ConnectionError("로그인에 성공했지만 clal을 찾지 못했다")
        await self._cookies.store(player_id, clal, profile.name)
        return LoginProfile(username=profile.name)

    async def _with_adapter[T](
        self,
        player_id: str,
        ctx: ToolContext | None,
        work: Callable[[MaimaiNetAdapterLike], Awaitable[T]],
        *,
        factory: AdapterFactory | None = None,
    ) -> T:
        """저장한 `clal`로 연결 하나를 열어 `work`를 한다. 인증 실패일 때만 만료로 보고 sega-linked를 떼고
        sega.login.expired를 낸 뒤 그대로 예외를 다시 던진다. 점검·네트워크·파서 오류는 로그인과 무관하므로 태그를
        건드리지 않는다."""
        clal = await self._cookies.clal(player_id)
        if clal is None:
            raise ConnectionError("로그인되어 있지 않다")

        adapter = (factory or self._adapter_factory)(clal)
        del clal
        try:
            return await work(adapter)
        except AuthenticationError:
            await self._cookies.expire(player_id, ctx)
            raise
        finally:
            await adapter.aclose()

    async def fetch_snapshot(self, player_id: str, *, ctx: ToolContext | None = None) -> SyncSnapshot:
        """프로필, 레이팅 대상곡, 최근 플레이, 난이도별 전체 기록을 한 번에 가져온다."""

        async def work(adapter: MaimaiNetAdapterLike) -> SyncSnapshot:
            profile, targets = await adapter.get_rating_targets()
            recent = await adapter.get_recent()
            lists = {code: await adapter.get_music_list(code) for code in LIST_DIFFICULTIES}
            return snapshot_from_pages(profile, targets, recent, lists)

        return await self._with_adapter(player_id, ctx, work)

    async def get_arcade_settings(self, player_id: str, *, ctx: ToolContext | None = None) -> GameSettings:
        """지금 계정의 인게임 설정(`arcade`·`settings`)."""

        async def work(adapter: MaimaiNetAdapterLike) -> GameSettings:
            return parse_settings(LexborHTMLParser(await adapter.get_user_option()), SETTINGS_FIELDS)

        return await self._with_adapter(player_id, ctx, work)

    async def update_arcade_settings(
        self, player_id: str, *, changes: dict[str, str], ctx: ToolContext | None = None
    ) -> GameSettings:
        """`changes`(attr → 표시 텍스트)만 바꾸고 나머지는 지금 값 그대로 다시 제출한다. 다시 읽어 실제로 반영된 값을
        돌려주고, 요청과 다르면 `ChangeNotAppliedError`다. 선택지에 없는 값은 `build_update_payload`의 ValueError."""

        async def work(adapter: MaimaiNetAdapterLike) -> GameSettings:
            html = await adapter.get_user_option()
            payload = build_update_payload(LexborHTMLParser(html), SETTINGS_FIELDS, changes)
            payload["token"] = form_token(html)
            await adapter.post_user_option(payload)
            settings = parse_settings(LexborHTMLParser(await adapter.get_user_option()), SETTINGS_FIELDS)
            raise_if_not_applied(changes, settings, SETTINGS_FIELDS, "maimai DX NET")
            return settings

        return await self._with_adapter(player_id, ctx, work)

    async def get_favorite_music(self, player_id: str, *, ctx: ToolContext | None = None) -> list[str]:
        """지금 즐겨찾기 곡 제목(곡 단위, DX/STD 구분 없음). 사이트의 곡 값은 불투명해서 제목으로 다룬다."""

        async def work(adapter: MaimaiNetAdapterLike) -> list[str]:
            return [entry.title for entry in parse_favorite_page(await adapter.get_favorite_page()) if entry.checked]

        return await self._with_adapter(player_id, ctx, work)

    async def set_favorite_music(
        self, player_id: str, titles: Sequence[str], *, ctx: ToolContext | None = None
    ) -> None:
        """즐겨찾기를 `titles`(추가·삭제를 반영한 전체 목록)로 통째로 바꾼다. 다시 읽어 다르면 `ChangeNotAppliedError`."""

        async def work(adapter: MaimaiNetAdapterLike) -> None:
            html = await adapter.get_favorite_page()
            await adapter.post_favorites(favorite_payload(parse_favorite_page(html), titles, form_token(html)))
            after = [entry.title for entry in parse_favorite_page(await adapter.get_favorite_page()) if entry.checked]
            if set(after) != set(titles):
                raise ChangeNotAppliedError(
                    f"maimai DX NET에 반영되지 않았어 — 보낸 곡 {len(set(titles))}개, 지금 즐겨찾기 {len(set(after))}개"
                )

        await self._with_adapter(player_id, ctx, work)

    async def play_activity(self, player_id: str, *, ctx: ToolContext | None = None) -> PlayActivity:
        """플레이 횟수 수집의 시작점: 플레이어 정보의 총 플레이 횟수(크레딧)와 최근 플레이 목록(곡 번호·시각)."""

        async def work(adapter: MaimaiNetAdapterLike) -> PlayActivity:
            total = await adapter.get_total_play_count()
            recent = await adapter.get_recent()
            return PlayActivity(
                total_plays=total,
                recent=[
                    RecentPlay(title=play.title, chart_type=play.chart_type, played_at=play.played_at, track=play.track,
                               difficulty=play.difficulty)
                    for play in recent
                ],
            )

        return await self._with_adapter(player_id, ctx, work)

    async def play_counts(
        self, player_id: str, charts: Sequence[tuple[str, str]], *, ctx: ToolContext | None = None
    ) -> dict[tuple[str, str], dict[str, int]]:
        """(제목, 채보 종류)마다 난이도 → 플레이 횟수(안 친 난이도는 없다 = 0회). 곡 목록에서 곡 상세로 가는 값을 찾고
        곡 상세를 동시에 받는다(초당 `PLAY_COUNT_RATE`번). 목록에 없는 곡(宴 등)이나 상세를 못 읽은 곡은 결과에 없다(모름).
        인증 실패는 만료로 처리하고 다시 던진다."""
        wanted = list(dict.fromkeys(charts))

        async def work(adapter: MaimaiNetAdapterLike) -> dict[tuple[str, str], dict[str, int]]:
            # 목록을 먼저 하나 받아 세션을 만든 뒤 상세를 동시에 받는다(풀린 세션에 동시에 다시 로그인하지 않게).
            index = await adapter.get_music_index()

            async def one(key: tuple[str, str]) -> tuple[tuple[str, str], dict[str, int]] | None:
                idx = index.get(key)
                if idx is None:
                    return None
                try:
                    counts = await adapter.get_play_counts(idx)
                except AuthenticationError:
                    raise
                except Exception:
                    logger.warning("maimai 곡 상세를 못 읽었다: %s", key, exc_info=True)
                    return None
                return key, {difficulty: n for (kind, difficulty), n in counts.items() if kind == key[1]}

            results = await asyncio.gather(*(one(key) for key in wanted))
            return dict(found for found in results if found is not None)

        return await self._with_adapter(player_id, ctx, work, factory=self._play_count_adapter_factory)
