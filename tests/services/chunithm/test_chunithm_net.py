# tests/services/chunithm/test_chunithm_net.py
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
from chuni_penguin.adapters.chunithm_net.parser import (
    parse_music_for_rating,
    parse_player_data,
)
from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError
from cryptography.fernet import Fernet
from selectolax.lexbor import LexborHTMLParser

from core.events import EventBus
from core.tool_types import ToolContext
from services.chunithm.chunithm_net import (
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


def _fixture_profile():
    return parse_player_data(LexborHTMLParser(_PROFILE_HTML))


def _fixture_best30():
    return parse_music_for_rating(LexborHTMLParser(_BEST_HTML))


def _fixture_new20():
    return parse_music_for_rating(LexborHTMLParser(_RECENT_HTML))


class _FakeAdapter:
    def __init__(
        self,
        *,
        fails: bool = False,
        error: Exception | None = None,
        logout_fails: bool = False,
        jar: str = "#LWP-Cookies-2.0\n",
    ) -> None:
        self.fails = fails
        # fails=True의 기본 실패는 "쿠키 만료" — 실제 어댑터가 인증 실패에서 던지는 타입이다.
        self.error = error if error is not None else AuthenticationError("쿠키 만료")
        self.logout_fails = logout_fails
        self.logged_out = False
        self._jar = jar

    @property
    def lwp_cookie_jar(self) -> str:
        return self._jar

    async def get_profile(self):
        if self.fails:
            raise self.error
        return _fixture_profile()

    async def get_best30(self):
        return _fixture_best30()

    async def get_new20(self):
        return _fixture_new20()

    async def get_recent_scores(self):
        return []

    async def logout(self) -> None:
        if self.logout_fails:
            raise ConnectionError("원격 로그아웃 실패")
        self.logged_out = True

    async def aclose(self) -> None:
        pass


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

    async def _record_expired(payload: object) -> None:
        received.append(payload)

    bus = EventBus()
    bus.register_notify(
        "chunithm.login.expired", owner_skill="test", fn=_record_expired
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

    async def _record_expired(payload: object) -> None:
        received.append(payload)

    bus = EventBus()
    bus.register_notify("chunithm.login.expired", owner_skill="test", fn=_record_expired)
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
