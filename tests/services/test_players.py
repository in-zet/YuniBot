from pathlib import Path

import aiosqlite
import pytest

from services.common.players import PlayersService

_MIGRATION = Path("migrations/core/001_players.sql").read_text()


@pytest.fixture
async def conn() -> aiosqlite.Connection:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_create_player_sets_first_seen(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    assert player.id
    assert player.first_seen_at
    assert player.primary_discord_id is None
    assert player.sync_report_public is True
    assert player.records_public is False


async def test_link_account_sets_primary_on_first_link(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    await service.link_account(player.id, "111")

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.primary_discord_id == "111"
    accounts = await service.accounts_for(player.id)
    assert [a.discord_id for a in accounts] == ["111"]
    assert accounts[0].is_primary is True


async def test_link_second_account_does_not_change_primary(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")

    await service.link_account(player.id, "222")

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.primary_discord_id == "111"
    accounts = {a.discord_id: a.is_primary for a in await service.accounts_for(player.id)}
    assert accounts == {"111": True, "222": False}


async def test_player_for_discord_resolves_linked_account(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")

    found = await service.player_for_discord("111")
    assert found is not None
    assert found.id == player.id

    assert await service.player_for_discord("unknown") is None


async def test_set_primary_requires_linked_account(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")
    await service.link_account(player.id, "222")

    await service.set_primary(player.id, "222")

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.primary_discord_id == "222"

    with pytest.raises(ValueError, match="연결되지 않은"):
        await service.set_primary(player.id, "999")


async def test_unlink_account_reassigns_primary(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")
    await service.link_account(player.id, "222")

    await service.unlink_account("111")

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.primary_discord_id == "222"
    assert await service.player_for_discord("111") is None


async def test_unlink_last_account_leaves_player_with_no_primary(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")

    await service.unlink_account("111")

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.primary_discord_id is None
    assert await service.accounts_for(player.id) == []


async def test_set_visibility_updates_single_flag(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    await service.set_visibility(player.id, "records_public", True)

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.records_public is True
    assert refreshed.memos_public is False
