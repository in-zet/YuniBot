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
        self.reactions: list[tuple[str, str, str]] = []
        self.channel_views: list[object | None] = []
        self.fail_send = False

    async def send_channel_message(self, channel_id: str, content: str, *, view: object | None = None) -> None:
        if self.fail_send:
            raise RuntimeError("전송 실패")
        self.channel_messages.append((channel_id, content))
        self.channel_views.append(view)

    async def open_dm_channel(self, discord_id: str) -> str:
        return f"dm-{discord_id}"

    async def send_dm(self, discord_id: str, content: str, *, view: object | None = None) -> None:
        self.dms.append((discord_id, content, view))

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.reactions.append((channel_id, message_id, emoji))


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


@pytest.mark.parametrize("bogus", ["999", "222> @everyone <@111", "<@333>", "nobody"])
async def test_mention_falls_back_to_primary_for_value_not_among_own_accounts(env, bogus: str) -> None:
    """리뷰(최종) Fix 6: 설정값이 이 플레이어의 실제 연결 계정 ID가 아니면(남의 계정, 해제된
    계정, 멘션 문법을 깨려는 값) 그대로 끼우지 않고 대표 계정으로 되돌아간다."""
    notify, players, settings, _ = env
    player = await players.create_player()
    await players.link_account(player.id, "111")
    await players.link_account(player.id, "222")
    other = await players.create_player()
    await players.link_account(other.id, "999")
    await settings.set_value(
        scope="player", scope_id=player.id, skill="account-link", name="알림을 받을 계정",
        value=bogus, updated_by=player.id,
    )

    mentioned = await notify.mention(player.id, "안녕하세요")

    assert mentioned == "<@111> 안녕하세요"


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
        channel_id="c1",
        on_close=closed.append,
        now=datetime.now(UTC),
    )

    assert session.skill_name == "chunithm-session"
    assert session.player_id == player.id


async def test_react_forwards_to_the_gateway(env) -> None:
    notify, gateway = env[0], env[-1]
    await notify.react("c1", "m1", "📝")
    assert gateway.reactions == [("c1", "m1", "📝")]


async def _linked_player(players: PlayersService, discord_id: str = "111") -> str:
    player = await players.create_player()
    await players.link_account(player.id, discord_id)
    return player.id


async def test_open_dm_thread_sends_opening_with_button_and_records_it(env) -> None:
    notify, players, _, gateway = env
    player_id = await _linked_player(players)
    closed: list[ThreadTranscript] = []

    session = await notify.open_dm_thread(
        skill_name="chunithm-session", player_id=player_id, opening="이번엔 어땠어?",
        on_close=closed.append, guidance="후기 지침", now=datetime.now(UTC),
    )

    assert session is not None
    assert session.channel_id == "dm-111"
    assert session.guidance == "후기 지침"
    assert gateway.channel_messages == [("dm-111", "이번엔 어땠어?")]
    assert [(m.content, m.is_yuni) for m in session.transcript] == [("이번엔 어땠어?", True)]
    assert notify.has_open_thread(player_id, "chunithm-session")


async def test_open_dm_thread_uses_the_end_button_factory(env) -> None:
    _, players, settings, gateway = env
    threads = ThreadManager()
    built: list[str] = []
    notify = NotifyService(
        gateway=gateway, players=players, settings=settings, threads=threads,
        end_button=lambda session: built.append(session.thread_id) or "view",
    )
    player_id = await _linked_player(players)

    session = await notify.open_dm_thread(
        skill_name="s", player_id=player_id, opening="안녕", on_close=lambda _t: None, now=datetime.now(UTC)
    )

    assert session is not None
    assert built == [session.thread_id]
    assert gateway.channel_views == ["view"]


async def test_open_dm_thread_returns_none_without_account_or_when_already_open(env) -> None:
    notify, players, _, gateway = env
    lonely = await players.create_player()
    assert await notify.open_dm_thread(
        skill_name="s", player_id=lonely.id, opening="안녕", on_close=lambda _t: None, now=datetime.now(UTC)
    ) is None

    player_id = await _linked_player(players)
    first = await notify.open_dm_thread(
        skill_name="s", player_id=player_id, opening="안녕", on_close=lambda _t: None, now=datetime.now(UTC)
    )
    second = await notify.open_dm_thread(
        skill_name="s", player_id=player_id, opening="또 안녕", on_close=lambda _t: None, now=datetime.now(UTC)
    )
    assert first is not None and second is None
    assert gateway.channel_messages == [("dm-111", "안녕")]


async def test_open_dm_thread_discards_the_session_when_the_opening_cannot_be_sent(env) -> None:
    notify, players, _, gateway = env
    player_id = await _linked_player(players)
    gateway.fail_send = True

    with pytest.raises(RuntimeError):
        await notify.open_dm_thread(
            skill_name="s", player_id=player_id, opening="안녕", on_close=lambda _t: None, now=datetime.now(UTC)
        )

    assert not notify.has_open_thread(player_id, "s")


async def test_close_thread_runs_the_close_callback(env) -> None:
    notify, players, _, _ = env
    player_id = await _linked_player(players)
    closed: list[ThreadTranscript] = []
    session = await notify.open_dm_thread(
        skill_name="s", player_id=player_id, opening="안녕", on_close=closed.append, now=datetime.now(UTC)
    )
    assert session is not None

    await notify.close_thread(session.thread_id, reason="explicit")

    assert [t.closed_reason for t in closed] == ["explicit"]
    assert not notify.has_open_thread(player_id, "s")
