# tests/services/chunithm/test_chunithm_net.py
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
from chuni_penguin.adapters.chunithm_net.parser import (
    parse_music_for_rating,
    parse_player_data,
)
from cryptography.fernet import Fernet
from selectolax.lexbor import LexborHTMLParser

from core.events import EventBus
from core.tool_types import ToolContext
from services.chunithm.chunithm_net import ChunithmNetService, SyncFetch
from services.common.players import PlayersService
from services.common.secrets import SecretsService

_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)
_COOKIES_MIGRATION = Path("migrations/core/007_chunithm_net_cookies.sql").read_text()

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
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.logged_out = False

    async def get_profile(self):
        if self.fails:
            raise ConnectionError("쿠키 만료")
        return _fixture_profile()

    async def get_best30(self):
        return _fixture_best30()

    async def get_new20(self):
        return _fixture_new20()

    async def get_recent_scores(self):
        return []

    async def logout(self) -> None:
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


def _make_service(conn, secrets, *, adapter: _FakeAdapter, players: PlayersService) -> ChunithmNetService:
    return ChunithmNetService(
        conn=conn, secrets=secrets, players=players, adapter_factory=lambda cookie: adapter
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

    with pytest.raises(ConnectionError):
        await service.login(player.id, "a" * 64, ctx)

    assert await players.tags_for(player.id) == set()


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
    bus = EventBus()
    bus.register_notify(
        "chunithm.login.expired", owner_skill="test", fn=lambda payload: received.append(payload)
    )
    ctx_with_bus = ToolContext(
        player_id=player.id, tags=frozenset(), services={}, event_bus=bus
    )

    with pytest.raises(ConnectionError):
        await service_for_sync.fetch_for_sync(player.id, ctx=ctx_with_bus)
    await bus.drain()

    assert len(received) == 1
    assert await players.tags_for(player.id) == set()
