# tests/services/test_notify.py
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from core.threads import ThreadManager, ThreadTranscript
from services.common.notify import NotifyService
from services.common.players import PlayersService
from services.common.settings import SettingsService

_MIGRATIONS = [
    Path("migrations/core/001_players.sql").read_text(),
    Path("migrations/core/002_player_tags.sql").read_text(),
    Path("migrations/core/003_settings.sql").read_text(),
]


class _FakeGateway:
    def __init__(self) -> None:
        self.channel_messages: list[tuple[str, str]] = []
        self.dms: list[tuple[str, str, object]] = []

    async def send_channel_message(self, channel_id: str, content: str) -> None:
        self.channel_messages.append((channel_id, content))

    async def send_dm(self, discord_id: str, content: str, *, view: object | None = None) -> None:
        self.dms.append((discord_id, content, view))


@pytest.fixture
async def env():
    conn = await aiosqlite.connect(":memory:")
    for migration in _MIGRATIONS:
        await conn.executescript(migration)
    await conn.commit()
    players = PlayersService(conn)
    settings = SettingsService(conn)
    gateway = _FakeGateway()
    notify = NotifyService(gateway=gateway, players=players, settings=settings, threads=ThreadManager())
    try:
        yield notify, players, settings, gateway
    finally:
        await conn.close()


async def test_post_sends_to_gateway(env) -> None:
    notify, _, _, gateway = env
    await notify.post("chan-1", "동기화 완료")
    assert gateway.channel_messages == [("chan-1", "동기화 완료")]


async def test_dm_uses_primary_account(env) -> None:
    notify, players, _, gateway = env
    player = await players.create_player()
    await players.link_account(player.id, "111")

    await notify.dm(player.id, "쿠키가 만료됐어요")

    assert gateway.dms == [("111", "쿠키가 만료됐어요", None)]


async def test_dm_noop_when_player_has_no_account(env) -> None:
    notify, players, _, gateway = env
    player = await players.create_player()

    await notify.dm(player.id, "아무도 못 받는 DM")

    assert gateway.dms == []


async def test_dm_passes_view_through(env) -> None:
    notify, players, _, gateway = env
    player = await players.create_player()
    await players.link_account(player.id, "111")

    sentinel_view = object()
    await notify.dm(player.id, "확인해 주세요", view=sentinel_view)

    assert gateway.dms == [("111", "확인해 주세요", sentinel_view)]


async def test_mention_defaults_to_primary_account(env) -> None:
    notify, players, _, _ = env
    player = await players.create_player()
    await players.link_account(player.id, "111")
    await players.link_account(player.id, "222")

    mentioned = await notify.mention(player.id, "안녕하세요")

    assert mentioned == "<@111> 안녕하세요"


async def test_mention_respects_all_accounts_setting(env) -> None:
    notify, players, settings, _ = env
    player = await players.create_player()
    await players.link_account(player.id, "111")
    await players.link_account(player.id, "222")
    await settings.set_value(
        scope="player", scope_id=player.id, skill="account-link", name="알림을 받을 계정",
        value="all", updated_by=player.id,
    )

    mentioned = await notify.mention(player.id, "안녕하세요")

    assert mentioned == "<@111> <@222> 안녕하세요"


async def test_mention_respects_specific_account_setting(env) -> None:
    notify, players, settings, _ = env
    player = await players.create_player()
    await players.link_account(player.id, "111")
    await players.link_account(player.id, "222")
    await settings.set_value(
        scope="player", scope_id=player.id, skill="account-link", name="알림을 받을 계정",
        value="222", updated_by=player.id,
    )

    mentioned = await notify.mention(player.id, "안녕하세요")

    assert mentioned == "<@222> 안녕하세요"


async def test_open_thread_delegates_to_thread_manager(env) -> None:
    notify, players, _, _ = env
    player = await players.create_player()
    closed: list[ThreadTranscript] = []

    class _Transport:
        async def send_message(self, text: str) -> None:
            pass

    session = await notify.open_thread(
        skill_name="chunithm-session",
        player_id=player.id,
        transport=_Transport(),
        on_close=closed.append,
        now=datetime.now(UTC),
    )

    assert session.skill_name == "chunithm-session"
    assert session.player_id == player.id
