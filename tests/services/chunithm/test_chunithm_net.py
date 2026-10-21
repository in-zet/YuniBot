# tests/services/chunithm/test_chunithm_net.py
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import aiosqlite
import pytest
from chuni_penguin.adapters.chunithm_net.parser import (
    parse_music_for_rating,
    parse_player_data,
)
from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError
from chuni_penguin.types import (
    Chart,
    ClearLamp,
    ComboLamp,
    Difficulty,
    Judgements,
    PersonalBest,
    RecentScore,
    Song,
)
from cryptography.fernet import Fernet
from selectolax.lexbor import LexborHTMLParser

from core.events import EventBus
from core.tool_types import ToolContext
from services.chunithm.arcade_settings import parse_arcade_settings
from services.chunithm.chunithm_net import (
    ChangeNotAppliedError,
    ChunithmNetService,
    SyncFetch,
    _build_lwp_cookie_jar,
    _default_adapter_factory,
    _default_credential_adapter_factory,
    _extract_clal,
)
from services.common.players import PlayersService
from services.common.secrets import SecretsService

_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)
_COOKIES_MIGRATION = (
    Path("migrations/core/007_chunithm_net_cookies.sql").read_text()
    + Path("migrations/core/010_chunithm_net_cookies_nickname.sql").read_text()
)

_PROFILE_HTML = Path("docs/reference/chunithm-net/player_data.html").read_text(encoding="utf-8")
_BEST_HTML = Path("docs/reference/chunithm-net/rating_detail_best.html").read_text(encoding="utf-8")
_RECENT_HTML = Path("docs/reference/chunithm-net/rating_detail_recent.html").read_text(encoding="utf-8")
_ARCADE_SETTINGS_HTML = Path("docs/reference/chunithm-net/arcade_settings.html").read_text(encoding="utf-8")


def _fixture_profile():
    return parse_player_data(LexborHTMLParser(_PROFILE_HTML))


def _fixture_best30():
    return parse_music_for_rating(LexborHTMLParser(_BEST_HTML))


def _fixture_new20():
    return parse_music_for_rating(LexborHTMLParser(_RECENT_HTML))


def _make_recent_score(n: int) -> RecentScore:
    """상세 판정 조회 테스트에 쓸 최소한의 RecentScore. judgements는 비워 둬서
    enrich_recent_with_detail이 채웠는지 아닌지로 구분할 수 있게 한다."""
    return RecentScore(
        song=Song(id=n, title=f"테스트곡 {n}"),
        chart=Chart(difficulty=Difficulty.master),
        score=1_000_000,
        clear_lamp=ClearLamp.clear,
        combo_lamp=ComboLamp.none,
        judgements=None,
    )


class _FakeAdapter:
    def __init__(
        self,
        *,
        fails: bool = False,
        error: Exception | None = None,
        logout_fails: bool = False,
        jar: str = "#LWP-Cookies-2.0\n",
        recent_scores: list[RecentScore] | None = None,
        personal_bests: dict[str, list[PersonalBest]] | None = None,
        favorite_music: list[int] | None = None,
        token: str | None = "tok123",
        ignores_writes: bool = False,
    ) -> None:
        self.fails = fails
        # fails=True의 기본 실패는 "쿠키 만료" — 실제 어댑터가 인증 실패에서 던지는 타입이다.
        self.error = error if error is not None else AuthenticationError("쿠키 만료")
        self.logout_fails = logout_fails
        self.logged_out = False
        self._jar = jar
        self._recent_scores = recent_scores if recent_scores is not None else []
        self._personal_bests = personal_bests if personal_bests is not None else {}
        self._favorite_music = favorite_music if favorite_music is not None else []
        self._token_value = token
        # 사이트가 요청을 조용히 거절하는 경우(2026-10-21 실기 — 토큰이 어긋나면 302만 돌려주고 안 바뀐다).
        self.ignores_writes = ignores_writes
        self.calls: list[str] = []
        self.requests: list[tuple[str, str, dict[str, object]]] = []
        self._settings_page = LexborHTMLParser(_ARCADE_SETTINGS_HTML)

    @property
    def lwp_cookie_jar(self) -> str:
        return self._jar

    @property
    def _token(self) -> str | None:
        return self._token_value

    async def get_favorite_music(self) -> list[int]:
        self.calls.append("get_favorite_music")
        if self.fails:
            raise self.error
        return list(self._favorite_music)

    async def set_favorite_music(self, ids) -> None:
        self.calls.append("set_favorite_music")
        if self.fails:
            raise self.error
        if not self.ignores_writes:
            self._favorite_music = list(ids)

    async def get_profile(self):
        if self.fails:
            raise self.error
        return _fixture_profile()

    async def get_best30(self):
        return _fixture_best30()

    async def get_new20(self):
        return _fixture_new20()

    async def get_recent_scores(self):
        if self.fails:
            raise self.error
        return self._recent_scores

    async def get_personal_bests(self, *, difficulty: Difficulty) -> list[PersonalBest]:
        return self._personal_bests.get(difficulty.short(), [])

    async def get_detailed_recent_score(self, score: RecentScore) -> RecentScore:
        # 실제 어댑터는 상세 페이지를 다시 읽어 judgements를 채운다 — 가짜에서는
        # 상세 조회가 다녀갔다는 것만 알 수 있으면 충분하다.
        if self.fails:
            raise self.error
        return replace(
            score, judgements=Judgements(justice_critical=0, justice=0, attack=0, miss=0)
        )

    async def logout(self) -> None:
        if self.logout_fails:
            raise ConnectionError("원격 로그아웃 실패")
        self.logged_out = True

    async def aclose(self) -> None:
        pass

    async def _request_as_soup(self, method: str, url: str, **kwargs: object) -> LexborHTMLParser:
        self.requests.append((method, url, dict(kwargs)))
        if self.fails:
            raise self.error
        if method == "POST" and not self.ignores_writes:
            data = cast("dict[str, str]", kwargs["data"])
            for select in self._settings_page.css("select"):
                name = select.attrs.get("name")
                if name not in data:
                    continue
                for option in select.css("option"):
                    if option.attrs.get("value") == data[name]:
                        option.attrs["selected"] = ""
                    elif "selected" in option.attrs:
                        del option.attrs["selected"]
        return self._settings_page


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_PLAYERS_MIGRATION + _COOKIES_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
def secrets() -> SecretsService:
    return SecretsService(Fernet.generate_key())


def _make_service(
    conn,
    secrets,
    *,
    adapter: _FakeAdapter,
    players: PlayersService,
    credential_adapter: _FakeAdapter | None = None,
) -> ChunithmNetService:
    return ChunithmNetService(
        conn=conn,
        secrets=secrets,
        players=players,
        adapter_factory=lambda cookie: adapter,
        credential_adapter_factory=(
            (lambda username, password: credential_adapter) if credential_adapter is not None else None
        ),
    )


async def test_login_success_stores_cookie_and_grants_linked_tag(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    bus = EventBus()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)

    profile = await service.login(player.id, "a" * 64, ctx)

    assert profile.username
    assert await players.tags_for(player.id) == {"chunithm-linked"}


async def test_login_failure_does_not_store_cookie_or_grant_tag(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    bus = EventBus()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)
    service = _make_service(conn, secrets, adapter=_FakeAdapter(fails=True), players=players)

    with pytest.raises(AuthenticationError):
        await service.login(player.id, "a" * 64, ctx)

    assert await players.tags_for(player.id) == set()


async def _stored_clal(conn: aiosqlite.Connection, secrets: SecretsService, player_id: str) -> str | None:
    conn.row_factory = aiosqlite.Row
    row = await (
        await conn.execute("SELECT encrypted_cookie FROM chunithm_net_cookies WHERE player_id = ?", (player_id,))
    ).fetchone()
    if row is None:
        return None
    return secrets.decrypt(row["encrypted_cookie"])


async def test_login_with_credentials_success_stores_only_clal_and_grants_linked_tag(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    bus = EventBus()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)
    credential_adapter = _FakeAdapter(jar=_build_lwp_cookie_jar("c" * 64))
    service = _make_service(
        conn, secrets, adapter=_FakeAdapter(), players=players, credential_adapter=credential_adapter
    )

    profile = await service.login_with_credentials(player.id, "my-sega-id", "my-password", ctx)

    assert profile.username
    assert await players.tags_for(player.id) == {"chunithm-linked"}
    assert await _stored_clal(conn, secrets, player.id) == "c" * 64


async def test_login_with_credentials_failure_does_not_store_anything(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    bus = EventBus()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)
    credential_adapter = _FakeAdapter(fails=True, error=AuthenticationError("sid/password 틀림"))
    service = _make_service(
        conn, secrets, adapter=_FakeAdapter(), players=players, credential_adapter=credential_adapter
    )

    with pytest.raises(AuthenticationError) as exc_info:
        await service.login_with_credentials(player.id, "my-sega-id", "my-password", ctx)

    assert "my-password" not in str(exc_info.value)
    assert await players.tags_for(player.id) == set()
    assert await _stored_clal(conn, secrets, player.id) is None


async def test_login_with_credentials_missing_clal_raises_without_storing(conn, secrets) -> None:
    # 로그인은 성공했는데(실제 어댑터가 깨진다거나) 받은 쿠키 jar에 clal이 없는 경우.
    # 숫자를 지어내지 않는 것과 같은 생각 — 없는 걸 있는 척 저장하지 않는다.
    players = PlayersService(conn)
    player = await players.create_player()
    bus = EventBus()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)
    credential_adapter = _FakeAdapter(jar="#LWP-Cookies-2.0\n")
    service = _make_service(
        conn, secrets, adapter=_FakeAdapter(), players=players, credential_adapter=credential_adapter
    )

    with pytest.raises(ConnectionError):
        await service.login_with_credentials(player.id, "my-sega-id", "my-password", ctx)

    assert await players.tags_for(player.id) == set()
    assert await _stored_clal(conn, secrets, player.id) is None


def test_extract_clal_finds_matching_cookie_among_others() -> None:
    jar = _build_lwp_cookie_jar("d" * 64)

    assert _extract_clal(jar) == "d" * 64


def test_extract_clal_returns_none_when_absent() -> None:
    assert _extract_clal("#LWP-Cookies-2.0\n") is None


async def test_default_credential_adapter_factory_constructs_without_network() -> None:
    """username/password로 실제 벤더링된 어댑터를 만든다 — 생성만으로는 네트워크를
    타지 않는다(login()용 _default_adapter_factory의 동급 테스트)."""
    adapter = _default_credential_adapter_factory("my-sega-id", "my-password")
    try:
        assert adapter is not None
    finally:
        await adapter.aclose()


async def test_status_reports_logged_in_after_login(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)
    await service.login(player.id, "a" * 64, ctx)

    status = await service.status(player.id)
    assert status.linked is True
    assert status.nickname


async def test_status_reports_not_linked_before_login(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)

    status = await service.status(player.id)
    assert status.linked is False


async def test_logout_removes_cookie_and_revokes_tag(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    adapter = _FakeAdapter()
    service = _make_service(conn, secrets, adapter=adapter, players=players)
    await service.login(player.id, "a" * 64, ctx)

    await service.logout(player.id, ctx)

    assert await players.tags_for(player.id) == set()
    assert adapter.logged_out is True
    status = await service.status(player.id)
    assert status.linked is False


async def test_fetch_for_sync_returns_profile_and_lists(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)
    await service.login(player.id, "a" * 64, ctx)

    fetch = await service.fetch_for_sync(player.id)

    assert isinstance(fetch, SyncFetch)
    assert len(fetch.best30) == 30
    assert len(fetch.new20) == 20


async def test_fetch_for_sync_on_connection_failure_expires_login(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    good_adapter = _FakeAdapter()
    service = _make_service(conn, secrets, adapter=good_adapter, players=players)
    await service.login(player.id, "a" * 64, ctx)

    failing = _FakeAdapter(fails=True)
    service_for_sync = _make_service(conn, secrets, adapter=failing, players=players)

    received: list[object] = []

    async def _record_expired(ctx: ToolContext, payload: object) -> None:
        received.append(payload)

    bus = EventBus()
    bus.register_notify(
        "chunithm.login.expired", owner_skill="test", fn=_record_expired, skill_services=[]
    )
    ctx_with_bus = ToolContext(
        player_id=player.id, tags=frozenset(), services={}, event_bus=bus
    )

    with pytest.raises(AuthenticationError):
        await service_for_sync.fetch_for_sync(player.id, ctx=ctx_with_bus)
    await bus.drain()

    assert len(received) == 1
    assert await players.tags_for(player.id) == set()


async def test_fetch_for_sync_on_non_auth_failure_keeps_login(conn, secrets) -> None:
    """점검이나 네트워크 오류는 로그인 만료가 아니다 — 태그를 떼거나 만료 알림을
    내면 안 되고, 예외만 그대로 올라가야 한다."""
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    await _make_service(conn, secrets, adapter=_FakeAdapter(), players=players).login(
        player.id, "a" * 64, ctx
    )

    received: list[object] = []

    async def _record_expired(ctx: ToolContext, payload: object) -> None:
        received.append(payload)

    bus = EventBus()
    bus.register_notify(
        "chunithm.login.expired", owner_skill="test", fn=_record_expired, skill_services=[]
    )
    ctx_with_bus = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)

    for error in (MaintenanceError(), ConnectionError("네트워크 끊김")):
        failing = _FakeAdapter(fails=True, error=error)
        service = _make_service(conn, secrets, adapter=failing, players=players)
        with pytest.raises(type(error)):
            await service.fetch_for_sync(player.id, ctx=ctx_with_bus)
    await bus.drain()

    assert received == []
    assert await players.tags_for(player.id) == {"chunithm-linked"}


async def test_logout_cleans_up_locally_even_when_remote_logout_fails(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    service = _make_service(conn, secrets, adapter=_FakeAdapter(logout_fails=True), players=players)
    await service.login(player.id, "a" * 64, ctx)

    await service.logout(player.id, ctx)

    assert await players.tags_for(player.id) == set()
    cursor = await conn.execute("SELECT COUNT(*) FROM chunithm_net_cookies WHERE player_id = ?", (player.id,))
    row = await cursor.fetchone()
    assert row is not None and row[0] == 0


def test_build_lwp_cookie_jar_produces_a_loadable_cookie() -> None:
    """원본 chuni-penguin(cogs/chunithm/auth.py)과 같은 쿠키여야 한다: clal은 SEGA
    인증 게이트웨이의 쿠키라 그 도메인/경로로 실어야 ChunithmNetAuth가 자동
    로그인할 때 같이 보내진다."""
    jar_str = _build_lwp_cookie_jar("a" * 64)
    assert jar_str.startswith("#LWP-Cookies-2.0\n")
    assert "domain=lng-tgk-aime-gw.am-all.net" in jar_str
    assert 'path="/common_auth"' in jar_str
    assert "expires=" in jar_str


def test_build_lwp_cookie_jar_strips_clal_prefix() -> None:
    jar_str = _build_lwp_cookie_jar("clal=" + "a" * 64)
    assert "clal=" + "a" * 64 + ";" in jar_str
    assert "clal=clal=" not in jar_str


async def test_default_adapter_factory_constructs_without_network_or_error() -> None:
    """실제 벤더링된 어댑터가 쓰는 LWPCookieJar 포맷으로 clal을 실어야 한다(domain/
    path/expires가 없으면 LoadError로 죽고, 그 메시지에 평문 쿠키가 들어간다 —
    가짜 어댑터만 쓰던 테스트로는 못 잡았던 실제 버그). 생성만으로는 네트워크를 타지 않는다."""
    adapter = _default_adapter_factory(_build_lwp_cookie_jar("a" * 64))
    try:
        assert adapter is not None
    finally:
        await adapter.aclose()


# 옛 형식(domain 없음)은 stdlib가 내부 AttributeError를 UserWarning으로도 알린다 —
# 이 테스트는 그 경로를 일부러 타므로 경고는 예상된 것이다(경고에 쿠키 값은 없다).
@pytest.mark.filterwarnings("ignore:http.cookiejar bug:UserWarning")
def test_default_adapter_factory_failure_does_not_chain_cookie() -> None:
    secret_line = "#LWP-Cookies-2.0\nSet-Cookie3: clal=" + "b" * 64 + "\n"
    with pytest.raises(ConnectionError) as exc_info:
        _ = _default_adapter_factory(secret_line)
    assert "b" * 64 not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


def test_build_lwp_cookie_jar_rejects_invalid_clal_without_embedding_it() -> None:
    with pytest.raises(ValueError) as exc_info:
        _ = _build_lwp_cookie_jar("not-a-valid-clal")
    assert "not-a-valid-clal" not in str(exc_info.value)


async def test_fetch_for_sync_fills_all_personal_bests_for_every_difficulty(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    personal_bests = {
        code: [PersonalBest(song=Song(id=idx, title=code), chart=Chart(difficulty=Difficulty(code)),
                             score=900_000, clear_lamp=ClearLamp.clear, combo_lamp=ComboLamp.none)]
        for idx, code in enumerate(("BAS", "ADV", "EXP", "MAS", "ULT", "WE"))
    }
    service = _make_service(
        conn, secrets, adapter=_FakeAdapter(personal_bests=personal_bests), players=players
    )
    await service.login(player.id, "a" * 64, ctx)

    fetch = await service.fetch_for_sync(player.id)

    assert set(fetch.all_personal_bests.keys()) == {"BAS", "ADV", "EXP", "MAS", "ULT", "WE"}
    for code in ("BAS", "ADV", "EXP", "MAS", "ULT", "WE"):
        assert [pb.song.title for pb in fetch.all_personal_bests[code]] == [code]


async def test_fetch_for_sync_on_connection_failure_does_not_partially_fill_personal_bests(
    conn, secrets
) -> None:
    """인증 실패면 PB 수집 도중이라도 부분 저장 없이 예외만 올라가야
    한다(기존 best30/new20과 같은 취급)."""
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    await _make_service(conn, secrets, adapter=_FakeAdapter(), players=players).login(
        player.id, "a" * 64, ctx
    )

    failing = _FakeAdapter(fails=True)
    service_for_sync = _make_service(conn, secrets, adapter=failing, players=players)

    with pytest.raises(AuthenticationError):
        await service_for_sync.fetch_for_sync(player.id)


async def test_get_recent_scores_returns_adapter_list(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    scores = [_make_recent_score(1), _make_recent_score(2)]
    service = _make_service(conn, secrets, adapter=_FakeAdapter(recent_scores=scores), players=players)
    await service.login(player.id, "a" * 64, ctx)

    result = await service.get_recent_scores(player.id)

    assert result == scores


async def test_get_recent_scores_without_login_raises_connection_error(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)

    with pytest.raises(ConnectionError):
        await service.get_recent_scores(player.id)


async def test_get_arcade_settings_returns_parsed_settings(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)
    await service.login(player.id, "a" * 64, ctx)

    result = await service.get_arcade_settings(player.id)

    assert result == parse_arcade_settings(LexborHTMLParser(_ARCADE_SETTINGS_HTML))


async def test_get_arcade_settings_without_login_raises_connection_error(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)

    with pytest.raises(ConnectionError):
        await service.get_arcade_settings(player.id)


async def test_update_arcade_settings_posts_full_payload_with_only_requested_changes(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    adapter = _FakeAdapter(token="tok-abc")
    service = _make_service(conn, secrets, adapter=adapter, players=players)
    await service.login(player.id, "a" * 64, ctx)

    result = await service.update_arcade_settings(player.id, changes={"speed": "9.00"})

    assert result.speed == "9.00"
    original = parse_arcade_settings(LexborHTMLParser(_ARCADE_SETTINGS_HTML))
    assert result.guide_sound == original.guide_sound  # 말하지 않은 항목은 그대로
    methods = [r[0] for r in adapter.requests]
    assert methods == ["GET", "POST", "GET"]
    _, post_url, post_kwargs = adapter.requests[1]
    assert post_url == "/mobile/home/userOption/updateUserOption/update/"
    payload = cast("dict[str, str]", post_kwargs["data"])
    assert payload["speed"] == "32"
    assert payload["token"] == "tok-abc"
    # 바꾸지 않은 항목은 지금 선택된 값 그대로 실려 간다.
    assert payload["fieldWallPosition"] == "3"


async def test_update_arcade_settings_without_login_raises_connection_error(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)

    with pytest.raises(ConnectionError):
        await service.update_arcade_settings(player.id, changes={"speed": "9.00"})


async def test_update_arcade_settings_unknown_value_raises_without_posting(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    adapter = _FakeAdapter()
    service = _make_service(conn, secrets, adapter=adapter, players=players)
    await service.login(player.id, "a" * 64, ctx)

    with pytest.raises(ValueError, match="가이드음"):
        await service.update_arcade_settings(player.id, changes={"guide_sound": "존재하지않는값"})

    assert [r[0] for r in adapter.requests] == ["GET"]


async def test_get_favorite_music_returns_adapter_list(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    service = _make_service(conn, secrets, adapter=_FakeAdapter(favorite_music=[3, 7]), players=players)
    await service.login(player.id, "a" * 64, ctx)

    assert await service.get_favorite_music(player.id) == [3, 7]


async def test_get_favorite_music_without_login_raises_connection_error(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)

    with pytest.raises(ConnectionError):
        await service.get_favorite_music(player.id)


async def test_set_favorite_music_replaces_list(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    adapter = _FakeAdapter(favorite_music=[3])
    service = _make_service(conn, secrets, adapter=adapter, players=players)
    await service.login(player.id, "a" * 64, ctx)

    await service.set_favorite_music(player.id, [3, 7])

    assert await service.get_favorite_music(player.id) == [3, 7]


async def test_set_favorite_music_without_login_raises_connection_error(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)

    with pytest.raises(ConnectionError):
        await service.set_favorite_music(player.id, [3])


async def test_enrich_recent_with_detail_replaces_only_the_first_limit_items(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)
    await service.login(player.id, "a" * 64, ctx)
    scores = [_make_recent_score(n) for n in range(4)]

    enriched = await service.enrich_recent_with_detail(player.id, scores, limit=2)

    assert [s.judgements is not None for s in enriched] == [True, True, False, False]
    # 원본 리스트는 건드리지 않는다.
    assert all(s.judgements is None for s in scores)


async def test_enrich_recent_with_detail_without_login_raises_connection_error(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)

    with pytest.raises(ConnectionError):
        await service.enrich_recent_with_detail(player.id, [_make_recent_score(1)], limit=1)


async def _linked_player_with_expiry_bus(conn, secrets):
    """로그인된 플레이어와, chunithm.login.expired를 (이벤트 대상 player_id, payload)로 기록하는 버스."""
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    await _make_service(conn, secrets, adapter=_FakeAdapter(), players=players).login(player.id, "a" * 64, ctx)

    received: list[tuple[str, object]] = []

    async def _record_expired(ctx: ToolContext, payload: object) -> None:
        received.append((ctx.player_id, payload))

    bus = EventBus()
    bus.register_notify("chunithm.login.expired", owner_skill="test", fn=_record_expired, skill_services=[])
    return players, player, bus, received


async def test_get_recent_scores_on_auth_failure_expires_login(conn, secrets) -> None:
    players, player, bus, received = await _linked_player_with_expiry_bus(conn, secrets)
    service = _make_service(conn, secrets, adapter=_FakeAdapter(fails=True), players=players)
    ctx_with_bus = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)

    with pytest.raises(AuthenticationError):
        await service.get_recent_scores(player.id, ctx=ctx_with_bus)
    await bus.drain()

    assert received == [(player.id, {"player_id": player.id})]
    assert await players.tags_for(player.id) == set()


async def test_get_arcade_settings_on_auth_failure_expires_login(conn, secrets) -> None:
    players, player, bus, received = await _linked_player_with_expiry_bus(conn, secrets)
    service = _make_service(conn, secrets, adapter=_FakeAdapter(fails=True), players=players)
    ctx_with_bus = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)

    with pytest.raises(AuthenticationError):
        await service.get_arcade_settings(player.id, ctx=ctx_with_bus)
    await bus.drain()

    assert received == [(player.id, {"player_id": player.id})]
    assert await players.tags_for(player.id) == set()


async def test_update_arcade_settings_on_auth_failure_expires_login(conn, secrets) -> None:
    players, player, bus, received = await _linked_player_with_expiry_bus(conn, secrets)
    service = _make_service(conn, secrets, adapter=_FakeAdapter(fails=True), players=players)
    ctx_with_bus = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)

    with pytest.raises(AuthenticationError):
        await service.update_arcade_settings(player.id, changes={"speed": "9.00"}, ctx=ctx_with_bus)
    await bus.drain()

    assert received == [(player.id, {"player_id": player.id})]
    assert await players.tags_for(player.id) == set()


async def test_get_favorite_music_on_auth_failure_expires_login(conn, secrets) -> None:
    players, player, bus, received = await _linked_player_with_expiry_bus(conn, secrets)
    service = _make_service(conn, secrets, adapter=_FakeAdapter(fails=True), players=players)
    ctx_with_bus = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)

    with pytest.raises(AuthenticationError):
        await service.get_favorite_music(player.id, ctx=ctx_with_bus)
    await bus.drain()

    assert received == [(player.id, {"player_id": player.id})]
    assert await players.tags_for(player.id) == set()


async def test_enrich_recent_with_detail_on_auth_failure_expires_login(conn, secrets) -> None:
    players, player, bus, received = await _linked_player_with_expiry_bus(conn, secrets)
    service = _make_service(conn, secrets, adapter=_FakeAdapter(fails=True), players=players)
    ctx_with_bus = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)

    with pytest.raises(AuthenticationError):
        await service.enrich_recent_with_detail(player.id, [_make_recent_score(1)], limit=1, ctx=ctx_with_bus)
    await bus.drain()

    assert received == [(player.id, {"player_id": player.id})]
    assert await players.tags_for(player.id) == set()


async def test_recent_auth_failure_for_other_player_expires_the_cookie_owner(conn, secrets) -> None:
    """/최근은 공개된 남의 기록도 본다 — 만료 처리와 이벤트 대상은 요청한 사람이 아니라 쿠키 주인이다."""
    players, owner, bus, received = await _linked_player_with_expiry_bus(conn, secrets)
    requester = await players.create_player()
    service = _make_service(conn, secrets, adapter=_FakeAdapter(fails=True), players=players)
    requester_ctx = ToolContext(player_id=requester.id, tags=frozenset(), services={}, event_bus=bus)

    with pytest.raises(AuthenticationError):
        await service.get_recent_scores(owner.id, ctx=requester_ctx)
    await bus.drain()

    assert received == [(owner.id, {"player_id": owner.id})]
    assert await players.tags_for(owner.id) == set()


async def test_recent_on_non_auth_failure_keeps_login(conn, secrets) -> None:
    players, player, bus, received = await _linked_player_with_expiry_bus(conn, secrets)
    ctx_with_bus = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)

    for error in (MaintenanceError(), ConnectionError("네트워크 끊김")):
        service = _make_service(conn, secrets, adapter=_FakeAdapter(fails=True, error=error), players=players)
        with pytest.raises(type(error)):
            await service.get_recent_scores(player.id, ctx=ctx_with_bus)
        with pytest.raises(type(error)):
            await service.enrich_recent_with_detail(player.id, [_make_recent_score(1)], limit=1, ctx=ctx_with_bus)
    await bus.drain()

    assert received == []
    assert await players.tags_for(player.id) == {"chunithm-linked"}


async def test_set_favorite_music_reads_first_so_the_request_token_exists_then_verifies(conn, secrets) -> None:
    """토큰은 로그인 뒤에야 생긴다 — 새 어댑터로 바로 쓰면 빈 토큰이 실려 사이트가 조용히 거절한다."""
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    adapter = _FakeAdapter(favorite_music=[3])
    service = _make_service(conn, secrets, adapter=adapter, players=players)
    await service.login(player.id, "a" * 64, ctx)
    adapter.calls.clear()

    await service.set_favorite_music(player.id, [3, 7])

    assert adapter.calls == ["get_favorite_music", "set_favorite_music", "get_favorite_music"]


async def test_set_favorite_music_raises_when_site_ignored_the_write(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    adapter = _FakeAdapter(favorite_music=[3], ignores_writes=True)
    service = _make_service(conn, secrets, adapter=adapter, players=players)
    await service.login(player.id, "a" * 64, ctx)

    with pytest.raises(ChangeNotAppliedError, match="반영되지 않았어"):
        await service.set_favorite_music(player.id, [3, 7])


async def test_update_arcade_settings_raises_when_site_ignored_the_write(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    adapter = _FakeAdapter(ignores_writes=True)
    service = _make_service(conn, secrets, adapter=adapter, players=players)
    await service.login(player.id, "a" * 64, ctx)

    with pytest.raises(ChangeNotAppliedError, match="스피드: 요청 9.00"):
        await service.update_arcade_settings(player.id, changes={"speed": "9.00"})
