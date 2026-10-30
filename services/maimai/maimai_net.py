# services/maimai/maimai_net.py
"""maimai DX NET(국제판) 어댑터와 동기화용 서비스.

로그인은 CHUNITHM과 같은 SEGA 공통 인증(`clal`)을 쓴다 — 쿠키는 `sega_cookies` 하나에 있고, 여기서는 읽기만
한다(로그인·로그아웃은 `sega-account`가 CHUNITHM 서비스로 한다). 복호화는 `SegaCookieStore`를 통해 이 서비스 안에서만
하고, 평문 `clal`은 어댑터를 만드는 스코프를 벗어나지 않는다."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

import aiolimiter
import httpx
from chuni_penguin.adapters._hooks import raise_on_server_errors
from chuni_penguin.adapters.errors import AuthenticationError

from core.tool_types import ToolContext, UserFacingError
from services.common.aime_auth import GATEWAY_HOST, AimeAuth, AimeSite
from services.common.sega_cookies import SegaCookieStore
from services.maimai import parser
from services.maimai.parser import MaimaiProfile, PlayEntry, RatingTargets, ScoreEntry
from services.maimai.sync_snapshot import snapshot_from_pages

if TYPE_CHECKING:
    import aiosqlite

    from services.common.players import PlayersService
    from services.common.secrets import SecretsService
    from services.game.sync_types import SyncSnapshot

logger = logging.getLogger(__name__)

_BASE_URL = "https://maimaidx-eng.com"
_MOBILE = "/maimai-mobile"
_ERROR_PATH = f"{_MOBILE}/error/"
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
        clal: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: aiolimiter.AsyncLimiter | None = None,
    ) -> None:
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
        self._client.cookies.set("clal", clal, domain=_CLAL_DOMAIN, path=_CLAL_PATH)
        self._client.auth = AimeAuth(self._client, SITE, needs_login=_session_lost)

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

    async def aclose(self) -> None:
        await self._client.aclose()


class MaimaiNetAdapterLike(Protocol):
    async def get_rating_targets(self) -> tuple[MaimaiProfile, RatingTargets]: ...
    async def get_recent(self) -> list[PlayEntry]: ...
    async def get_music_list(self, difficulty: str) -> list[ScoreEntry]: ...
    async def aclose(self) -> None: ...


AdapterFactory = Callable[[str], MaimaiNetAdapterLike]


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
    ) -> None:
        self._cookies = SegaCookieStore(conn=conn, secrets=secrets, players=players)
        self._adapter_factory: AdapterFactory = adapter_factory or MaimaiNetAdapter

    async def fetch_snapshot(self, player_id: str, *, ctx: ToolContext | None = None) -> SyncSnapshot:
        """프로필, 레이팅 대상곡, 최근 플레이, 난이도별 전체 기록을 한 번에 가져온다. 인증 실패일 때만 만료로 보고
        sega-linked를 떼고 sega.login.expired를 낸 뒤 그대로 예외를 다시 던진다. 점검·네트워크·파서 오류는
        로그인과 무관하므로 태그를 건드리지 않는다."""
        clal = await self._cookies.clal(player_id)
        if clal is None:
            raise ConnectionError("로그인되어 있지 않다")

        adapter = self._adapter_factory(clal)
        del clal
        try:
            profile, targets = await adapter.get_rating_targets()
            recent = await adapter.get_recent()
            lists = {code: await adapter.get_music_list(code) for code in LIST_DIFFICULTIES}
        except AuthenticationError:
            await self._cookies.expire(player_id, ctx)
            raise
        finally:
            await adapter.aclose()
        return snapshot_from_pages(profile, targets, recent, lists)

