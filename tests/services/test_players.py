from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.common.players import PlayersService

_MIGRATION = Path("migrations/core/001_players.sql").read_text()
_TAGS_MIGRATION = Path("migrations/core/002_player_tags.sql").read_text()


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATION)
    await connection.executescript(_TAGS_MIGRATION)
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


async def test_grant_and_revoke_tag(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    await service.grant_tag(player.id, "tester", source="admin:111")
    assert await service.tags_for(player.id) == {"tester"}
    assert await service.tag_source(player.id, "tester") == "admin:111"

    await service.revoke_tag(player.id, "tester")
    assert await service.tags_for(player.id) == set()


async def test_grant_tag_rejects_status_tags(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    with pytest.raises(ValueError, match="상태 태그"):
        await service.grant_tag(player.id, "sega-linked", source="admin:111")


async def test_players_with_tag_filters_by_source(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    env_admin = await service.create_player()
    manual_admin = await service.create_player()
    await service.grant_tag(env_admin.id, "admin", source="env_bootstrap")
    await service.grant_tag(manual_admin.id, "admin", source="admin:111")

    assert set(await service.players_with_tag("admin")) == {env_admin.id, manual_admin.id}
    assert await service.players_with_tag("admin", source="env_bootstrap") == [env_admin.id]


async def test_resolve_player_ref_self(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})

    resolved = await service.resolve("나", ctx)
    assert resolved.id == player.id


async def test_resolve_player_ref_mention(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    target = await service.create_player()
    await service.link_account(target.id, "123456789012345678")
    caller = await service.create_player()
    ctx = ToolContext(player_id=caller.id, tags=frozenset(), services={})

    resolved = await service.resolve("<@123456789012345678>", ctx)
    assert resolved.id == target.id

    resolved_bare = await service.resolve("<@!123456789012345678>", ctx)
    assert resolved_bare.id == target.id


async def test_resolve_player_ref_unknown_mention_fails(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    caller = await service.create_player()
    ctx = ToolContext(player_id=caller.id, tags=frozenset(), services={})

    with pytest.raises(ResolutionError, match="모름"):
        await service.resolve("<@999999999999999999>", ctx)


async def test_dissolve_player_removes_accounts_and_tags(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")
    await service.grant_tag(player.id, "tester", source="admin:1")

    await service.dissolve_player(player.id)

    assert await service.player_for_discord("111") is None
    assert await service.tags_for(player.id) == set()


async def test_resolve_player_ref_free_text_fails(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    caller = await service.create_player()
    ctx = ToolContext(player_id=caller.id, tags=frozenset(), services={})

    with pytest.raises(ResolutionError, match="모름"):
        await service.resolve("크로노", ctx)


async def test_set_status_tag_allows_only_status_tags(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    await service.set_status_tag(player.id, "sega-linked", source="chunithm_net:login")
    assert await service.tags_for(player.id) == {"sega-linked"}
    assert await service.tag_source(player.id, "sega-linked") == "chunithm_net:login"

    with pytest.raises(ValueError, match="상태 태그가 아니다"):
        await service.set_status_tag(player.id, "admin", source="x")


async def test_revoke_status_tag_allows_only_status_tags(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.set_status_tag(player.id, "chunithm-synced", source="records:first_sync")

    await service.revoke_status_tag(player.id, "chunithm-synced")
    assert await service.tags_for(player.id) == set()

    with pytest.raises(ValueError, match="상태 태그가 아니다"):
        await service.revoke_status_tag(player.id, "tester")
