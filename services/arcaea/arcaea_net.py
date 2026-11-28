# services/arcaea/arcaea_net.py
"""Arcaea Online(웹 API `webapi.lowiro.com`) 접속과 동기화용 서비스(19단계 2단계, 2026-11-28 사용자 결정).

로그인 쿠키는 `sid` 하나다. 아이디(또는 이메일)·비밀번호로 봇이 한 번 로그인해 받거나, 플레이어가 브라우저에서 꺼낸 쿠키를
직접 넣는다(구글·애플 가입 계정은 비밀번호가 없다). 쿠키는 `arcaea_sessions`에 암호화해 두고 복호화는 이 서비스 안에서만
한다. 기록은 Arcaea Online 구독자만 읽을 수 있다. 응답 모양과 근거는 `docs/reference/arcaea-online/README.md`."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

import aiolimiter
import httpx
from chuni_penguin.adapters.errors import AuthenticationError

from core.tool_types import ToolContext, UserFacingError
from services.arcaea.profile import ARCAEA
from services.arcaea.rating import CLEAR_BONUS, score_modifier
from services.arcaea.songdb import ObservedConst
from services.common.account_cookies import (
    ARCAEA_ACCOUNT,
    AccountCookieStore,
    LoginStatus,
)
from services.game.sync_types import ChartPlay, ProfileSnapshot, SyncSnapshot

if TYPE_CHECKING:
    import aiosqlite

    from services.arcaea.songdb import ArcaeaSongDbService
    from services.common.players import PlayersService
    from services.common.secrets import SecretsService

logger = logging.getLogger(__name__)

_API = "https://webapi.lowiro.com"
_SITE = "https://arcaea.lowiro.com"
_COOKIE_NAME = "sid"
# 초당 요청 수(2026-11-28 사용자 결정). 동기화는 난이도마다 10곡씩 페이지를 넘겨 친 곡 전부를 받는다.
REQUEST_RATE = 5
# API의 난이도 번호 → 코드. 3이 BYD, 4가 ETR이다(곡 DB와 대조해 확인).
DIFFICULTY_CODES: tuple[str, ...] = ("PST", "PRS", "FTR", "BYD", "ETR")
# clear_type: 0 TRACK LOST, 1 NORMAL, 2 FULL RECALL, 3 PURE MEMORY, 4 EASY, 5 HARD. FR·PM은 게이지를 알 수 없다.
_CLEAR_LAMPS = {0: "TRACK LOST", 1: "NORMAL CLEAR", 4: "EASY CLEAR", 5: "HARD CLEAR"}
_COMBO_LAMPS = {2: "FULL RECALL", 3: "PURE MEMORY"}
_RANKS = ((9_900_000, "EX+"), (9_800_000, "EX"), (9_500_000, "AA"), (9_200_000, "A"), (8_900_000, "B"), (8_600_000, "C"))
# 플레이 시각을 세션 날짜로 나눌 시간대(지인 서버 — 한국·일본 시간).
_LOCAL = timezone(timedelta(hours=9))
_MAX_PAGES = 200  # 난이도 하나에 2,000곡 — 넘으면 응답이 이상한 것이다


class ArcaeaOnlineError(UserFacingError):
    """Arcaea Online이 요청을 거절했다(로그인 풀림이 아님). 메시지는 사용자에게 그대로 보여 줄 수 있다."""


class NotSubscribedError(ArcaeaOnlineError):
    def __init__(self) -> None:
        super().__init__("Arcaea Online 구독이 없어서 기록을 읽을 수 없어. 구독 중인 계정으로 로그인해 줘.")


def rank_of(score: int) -> str:
    return next((name for floor, name in _RANKS if score >= floor), "D")


def _api_error(response: httpx.Response) -> None:
    """401은 로그인 풀림, 그 밖의 실패는 사용자에게 보일 오류로 바꾼다."""
    if response.status_code == 401:
        raise AuthenticationError("Arcaea Online 로그인이 풀렸다")
    if response.status_code == 403:
        raise NotSubscribedError
    if response.status_code >= 400:
        raise ArcaeaOnlineError(f"Arcaea Online이 요청을 거절했어(HTTP {response.status_code}). 잠시 뒤에 다시 해 줘.")


def _value(response: httpx.Response) -> Any:
    _api_error(response)
    body = response.json()
    if not isinstance(body, dict) or not body.get("success"):  # pyright: ignore[reportUnknownMemberType]
        code = body.get("error_code") if isinstance(body, dict) else None  # pyright: ignore[reportUnknownMemberType]
        raise ArcaeaOnlineError(f"Arcaea Online이 요청을 거절했어(오류 {code}). 잠시 뒤에 다시 해 줘.")
    return body.get("value")  # pyright: ignore[reportUnknownMemberType]


class ArcaeaOnlineAdapter:
    """한 번의 작업 동안 쓰는 Arcaea Online 연결. 쿠키는 이 연결의 쿠키 상자에만 있다."""

    def __init__(
        self,
        sid: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: aiolimiter.AsyncLimiter | None = None,
    ) -> None:
        limiter = limiter if limiter is not None else aiolimiter.AsyncLimiter(REQUEST_RATE, 1)

        async def throttle(request: httpx.Request) -> None:
            await limiter.acquire()

        self._client = httpx.AsyncClient(
            base_url=_API,
            timeout=httpx.Timeout(30.0),
            transport=transport,
            event_hooks={"request": [throttle]},
            headers={
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/114.0",
                "accept": "application/json",
                "origin": _SITE,
                "referer": f"{_SITE}/",
            },
        )
        if sid is not None:
            self._client.cookies.set(_COOKIE_NAME, sid, domain=".lowiro.com", path="/")

    def session_cookie(self) -> str | None:
        """이 연결이 가진 `sid`. 로그인 뒤 저장할 때만 꺼낸다."""
        for cookie in self._client.cookies.jar:
            if cookie.name == _COOKIE_NAME:
                return cookie.value
        return None

    async def login(self, username: str, password: str) -> None:
        """사이트 로그인 폼과 같은 요청(`POST /auth/login`, 아이디나 이메일·비밀번호). 틀리면 AuthenticationError."""
        await self._client.get("/auth/me")  # csrf 쿠키를 먼저 받는다(있으면 헤더로 돌려준다)
        headers = {"x-csrf-token": csrf} if (csrf := self._client.cookies.get("csrf")) else {}
        response = await self._client.post("/auth/login", json={"email": username, "password": password}, headers=headers)
        if response.status_code in (400, 401, 403):
            raise AuthenticationError("Arcaea 로그인 실패")
        _value(response)

    async def get_user(self) -> dict[str, Any]:
        return _value(await self._client.get("/webapi/user/me"))

    async def get_best(self) -> list[dict[str, Any]]:
        """포텐셜에 들어가는 베스트(50개, 곡별 포텐셜 `rating` 포함)."""
        return list(_value(await self._client.get("/webapi/score/rating/me"))["best_rated_scores"])

    async def get_scores(self, difficulty: int, page: int) -> tuple[list[dict[str, Any]], int]:
        """한 난이도의 기록 한 페이지(10개, 최근 순)와 그 난이도의 친 곡 수."""
        value = _value(await self._client.get(
            "/webapi/score/song/me/all", params={"difficulty": difficulty, "page": page, "sort": "date", "term": ""}
        ))
        return list(value["scores"]), int(value["count"])

    async def aclose(self) -> None:
        await self._client.aclose()


class ArcaeaAdapterLike(Protocol):
    def session_cookie(self) -> str | None: ...
    async def login(self, username: str, password: str) -> None: ...
    async def get_user(self) -> dict[str, Any]: ...
    async def get_best(self) -> list[dict[str, Any]]: ...
    async def get_scores(self, difficulty: int, page: int) -> tuple[list[dict[str, Any]], int]: ...
    async def aclose(self) -> None: ...


AdapterFactory = Callable[[str | None], ArcaeaAdapterLike]


@dataclass(slots=True, kw_only=True, frozen=True)
class LoginProfile:
    username: str


@dataclass(slots=True, kw_only=True)
class _Fetched:
    user: dict[str, Any]
    best: list[dict[str, Any]]
    scores: list[dict[str, Any]]


# ---------------------------------------------------------------- 응답 → 게임 중립 자료형


def _titles(entry: Mapping[str, Any]) -> list[str]:
    title = entry.get("title") or {}
    return [text for key in ("ja", "en") if isinstance(text := title.get(key), str) and text]


def _played_at(entry: Mapping[str, Any]) -> datetime | None:
    millis = entry.get("time_played")
    return datetime.fromtimestamp(millis / 1000, tz=UTC).astimezone(_LOCAL) if isinstance(millis, int) and millis > 0 else None


def _lamps(entry: Mapping[str, Any]) -> tuple[str, str]:
    """(클리어 램프, 콤보 램프). 기록의 클리어 종류와 그 채보의 가장 좋은 클리어 종류 중 나은 쪽을 쓴다."""
    kinds = [kind for kind in (entry.get("clear_type"), entry.get("best_clear_type")) if isinstance(kind, int)]
    combo = max((_COMBO_LAMPS[kind] for kind in kinds if kind in _COMBO_LAMPS),
                key=ARCAEA.combo_lamp_order.__getitem__, default="")
    clear = max((_CLEAR_LAMPS[kind] for kind in kinds if kind in _CLEAR_LAMPS),
                key=ARCAEA.clear_lamp_order.__getitem__, default="")
    if combo and clear == "TRACK LOST":
        clear = ""  # FR·PM이면 클리어한 것이다(게이지는 모른다)
    return clear, combo


def _difficulty(entry: Mapping[str, Any]) -> str | None:
    index = entry.get("difficulty")
    return DIFFICULTY_CODES[index] if isinstance(index, int) and 0 <= index < len(DIFFICULTY_CODES) else None


def observed_const(entry: Mapping[str, Any]) -> float | None:
    """곡별 포텐셜에서 거꾸로 낸 상수(소수 첫째 자리). 맞아떨어지지 않으면 None — 공식이 바뀐 것일 수 있다."""
    rating, score = entry.get("rating"), entry.get("score")
    if not isinstance(rating, (int, float)) or not isinstance(score, int):
        return None
    bonus = CLEAR_BONUS if entry.get("clear_type") != 0 else Decimal(0)
    implied = Decimal(str(rating)) - score_modifier(score) - bonus
    rounded = implied.quantize(Decimal("0.1"))
    if abs(implied - rounded) > Decimal("0.001") or rounded <= 0:
        return None
    return float(rounded)


class _Converter:
    """응답 항목 → `ChartPlay`. 제목은 곡 DB 제목으로 바꾼다(같은 곡 id는 한 번만 찾는다)."""

    def __init__(self, songdb: ArcaeaSongDbService) -> None:
        self._songdb = songdb
        self._titles: dict[str, str] = {}

    def title(self, entry: Mapping[str, Any]) -> str:
        song_id = str(entry.get("song_id") or "")
        if song_id not in self._titles:
            self._titles[song_id] = self._songdb.title_for(_titles(entry), str(entry.get("artist") or "")) or song_id
        return self._titles[song_id]

    def play(self, entry: Mapping[str, Any], *, with_time: bool) -> ChartPlay | None:
        difficulty, score = _difficulty(entry), entry.get("score")
        if difficulty is None or not isinstance(score, int):
            return None
        clear, combo = _lamps(entry)
        return ChartPlay(
            song_title=self.title(entry), difficulty=difficulty, score=score, rank=rank_of(score),
            clear_lamp=clear, combo_lamp=combo, achieved_at=_played_at(entry) if with_time else None,
        )


def snapshot_from(fetched: _Fetched, converter: _Converter) -> SyncSnapshot:
    """프로필 포텐셜은 ×1000 정수다(11366 = 11.366). 숨김 등으로 값이 없으면 대조하지 않는다. 최근 플레이 목록은 따로
    없어서(마지막 1판뿐) 기록마다 붙은 시각을 최근 플레이로 쓴다 — 직전 동기화 뒤에 오른 기록이 그날 세션이 된다."""
    rating = fetched.user.get("rating")
    displayed = (Decimal(rating) / 1000).quantize(Decimal("0.001")) if isinstance(rating, int) and rating >= 0 else None
    name = fetched.user.get("display_name") or fetched.user.get("name") or ""
    best = [play for entry in fetched.best if (play := converter.play(entry, with_time=False)) is not None]
    plays = [play for entry in fetched.scores if (play := converter.play(entry, with_time=False)) is not None]
    recent_entries = [*fetched.scores, *(fetched.user.get("recent_score") or [])]
    recent = [play for entry in recent_entries if (play := converter.play(entry, with_time=True)) is not None
              and play.achieved_at is not None]
    return SyncSnapshot(
        profile=ProfileSnapshot(username=str(name), displayed_rating=displayed),
        best=best, new=[], recent=recent, all_plays=plays,
    )


# ---------------------------------------------------------------- 서비스


class ArcaeaNetService:
    """플레이어의 Arcaea 기록을 가져온다. 스킬에는 다른 게임 NET 서비스처럼 `chunithm_net` 자리로 들어간다."""

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        secrets: SecretsService,
        players: PlayersService,
        songdb: ArcaeaSongDbService,
        save_play_counts: Callable[[str, Mapping[tuple[str, str], dict[str, int]]], Awaitable[None]] | None = None,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self._cookies = AccountCookieStore(conn=conn, secrets=secrets, players=players, kind=ARCAEA_ACCOUNT)
        self._songdb = songdb
        self._save_play_counts = save_play_counts
        self._adapter_factory: AdapterFactory = adapter_factory or (lambda sid: ArcaeaOnlineAdapter(sid))

    async def status(self, player_id: str) -> LoginStatus:
        return await self._cookies.status(player_id)

    async def login_with_credentials(self, player_id: str, username: str, password: str) -> LoginProfile:
        """아이디(또는 이메일)·비밀번호로 한 번 로그인해 `sid`만 저장한다. 아이디·비밀번호는 저장하지 않는다."""
        adapter = self._adapter_factory(None)
        try:
            await adapter.login(username, password)
            user = await adapter.get_user()
            sid = adapter.session_cookie()
        finally:
            await adapter.aclose()
        if sid is None:
            raise ConnectionError("로그인에 성공했지만 sid를 찾지 못했다")
        return await self._store(player_id, sid, user)

    async def login_with_cookie(self, player_id: str, cookie: str) -> LoginProfile:
        """플레이어가 넣은 쿠키로 프로필을 읽어 보고 되면 저장한다. `sid=…; 다른=…`처럼 통째로 붙여도 `sid`만 꺼낸다.
        쿠키가 틀렸거나 만료면 AuthenticationError다."""
        sid = parse_sid(cookie)
        if sid is None:
            raise AuthenticationError("쿠키에 sid가 없다")
        adapter = self._adapter_factory(sid)
        try:
            user = await adapter.get_user()
        finally:
            await adapter.aclose()
        return await self._store(player_id, sid, user)

    async def _store(self, player_id: str, sid: str, user: Mapping[str, Any]) -> LoginProfile:
        name = str(user.get("display_name") or user.get("name") or "")
        await self._cookies.store(player_id, sid, name)
        return LoginProfile(username=name)

    async def logout(self, player_id: str, ctx: ToolContext | None = None) -> None:
        """저장한 쿠키를 지운다(원격 로그아웃은 하지 않는다 — 브라우저에서 꺼낸 쿠키라면 그 브라우저도 로그아웃된다)."""
        await self._cookies.delete(player_id)

    async def _with_adapter[T](
        self, player_id: str, ctx: ToolContext | None, work: Callable[[ArcaeaAdapterLike], Awaitable[T]]
    ) -> T:
        """저장한 쿠키로 연결 하나를 열어 `work`를 한다. 인증 실패일 때만 만료로 보고 arcaea-linked를 떼고
        arcaea.login.expired를 낸 뒤 다시 던진다."""
        sid = await self._cookies.cookie(player_id)
        if sid is None:
            raise ConnectionError("로그인되어 있지 않다")
        adapter = self._adapter_factory(sid)
        del sid
        try:
            return await work(adapter)
        except AuthenticationError:
            await self._cookies.expire(player_id, ctx)
            raise
        finally:
            await adapter.aclose()

    async def fetch_snapshot(self, player_id: str, *, ctx: ToolContext | None = None) -> SyncSnapshot:
        """프로필, 베스트, 난이도별 친 곡 전부를 받는다(초당 `REQUEST_RATE`번). 베스트의 곡별 포텐셜로 곡 DB 상수를 고치고,
        기록마다 붙은 올해 플레이 횟수를 저장한다."""

        async def work(adapter: ArcaeaAdapterLike) -> _Fetched:
            user = await adapter.get_user()
            best = await adapter.get_best()
            scores: list[dict[str, Any]] = []
            for index in range(len(DIFFICULTY_CODES)):
                scores += await _all_pages(adapter, index)
            return _Fetched(user=user, best=best, scores=scores)

        fetched = await self._with_adapter(player_id, ctx, work)
        converter = _Converter(self._songdb)
        await self._learn(fetched.best, converter)
        if self._save_play_counts is not None:
            await self._save_play_counts(player_id, yearly_play_counts(fetched.scores, converter))
        return snapshot_from(fetched, converter)

    async def _learn(self, best: Sequence[Mapping[str, Any]], converter: _Converter) -> None:
        observed = [
            ObservedConst(song_title=converter.title(entry), difficulty=difficulty, const=const,
                          artist=str(entry.get("artist") or ""))
            for entry in best
            if (difficulty := _difficulty(entry)) is not None and (const := observed_const(entry)) is not None
        ]
        await self._songdb.learn_consts(observed)


async def _all_pages(adapter: ArcaeaAdapterLike, difficulty: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for page in range(1, _MAX_PAGES + 1):
        scores, count = await adapter.get_scores(difficulty, page)
        entries += scores
        if not scores or len(entries) >= count:
            break
    return entries


def yearly_play_counts(
    scores: Sequence[Mapping[str, Any]], converter: _Converter
) -> dict[tuple[str, str], dict[str, int]]:
    """(곡 DB 제목, "") → 난이도 → 올해 플레이 횟수(`yearly_play_count`)."""
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for entry in scores:
        difficulty, count = _difficulty(entry), entry.get("yearly_play_count")
        if difficulty is not None and isinstance(count, int):
            counts.setdefault((converter.title(entry), ""), {})[difficulty] = count
    return counts


def parse_sid(cookie: str) -> str | None:
    """붙여 넣은 쿠키 글에서 `sid` 값. 값만 붙였으면(= 없음) 그 글 전체를 sid로 본다."""
    text = cookie.strip().removeprefix("Cookie:").strip()
    if "=" not in text:
        return text or None
    for part in text.split(";"):
        name, _, value = part.strip().partition("=")
        if name == _COOKIE_NAME and value:
            return value
    return None
