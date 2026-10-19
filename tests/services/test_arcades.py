# tests/services/test_arcades.py
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.common.arcades import ArcadesService

_MIGRATIONS = [
    Path("migrations/core/005_arcades.sql").read_text(),
    Path("migrations/core/017_arcade_aliases_guild_scope.sql").read_text(),
]


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    for migration in _MIGRATIONS:
        await connection.executescript(migration)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_create_and_list_for_guild(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")

    listed = await service.list_for_guild("g1")
    assert [a.name for a in listed] == ["동인천 로지텍"]
    assert arcade.guild_id == "g1"


async def test_create_duplicate_name_in_same_guild_fails(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")

    with pytest.raises(ValueError, match="이미"):
        await service.create(guild_id="g1", name="동인천 로지텍", created_by="p2")


async def test_resolve_exact_name_match(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")

    resolved = await service.resolve("동인천 로지텍", ctx)
    assert resolved.id == arcade.id


async def test_resolve_uses_player_alias(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")
    await service.add_alias(arcade.id, guild_id="g1", player_id="p1", alias="동로")
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")

    resolved = await service.resolve("동로", ctx)
    assert resolved.id == arcade.id

    other_ctx = ToolContext(player_id="p2", tags=frozenset(), services={}, guild_id="g1")
    with pytest.raises(ResolutionError, match="없음"):
        await service.resolve("동로", other_ctx)


async def test_alias_same_text_in_different_guilds_does_not_collide(conn: aiosqlite.Connection) -> None:
    """같은 플레이어가 서버 A·B에서 같은 글자의 별칭을 각각 다른 오락실에 등록해도
    서로 덮어쓰지 않는다(guild_id가 PK에 없을 때는 ON CONFLICT가 같은 행을 덮어썼다)."""
    service = ArcadesService(conn)
    arcade_a = await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")
    arcade_b = await service.create(guild_id="g2", name="부산 오락실", created_by="p1")
    await service.add_alias(arcade_a.id, guild_id="g1", player_id="p1", alias="단골집")
    await service.add_alias(arcade_b.id, guild_id="g2", player_id="p1", alias="단골집")

    ctx_a = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    ctx_b = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g2")
    assert (await service.resolve("단골집", ctx_a)).id == arcade_a.id
    assert (await service.resolve("단골집", ctx_b)).id == arcade_b.id


async def test_resolve_ambiguous_returns_candidates(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")
    await service.create(guild_id="g1", name="동인천 아이플러스", created_by="p1")
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")

    with pytest.raises(ResolutionError) as exc_info:
        await service.resolve("동인천", ctx)
    assert set(exc_info.value.candidates) == {"동인천 로지텍", "동인천 아이플러스"}


async def test_resolve_no_match_raises_not_found(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")

    with pytest.raises(ResolutionError, match="없음"):
        await service.resolve("없는오락실", ctx)


async def test_resolve_requires_guild_context(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id=None)

    with pytest.raises(ResolutionError, match="서버"):
        await service.resolve("동인천 로지텍", ctx)


async def test_record_and_list_visits(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")

    await service.record_visit(arcade.id, player_id="p1", game="chunithm")
    visits = await service.visits_for(player_id="p1", game="chunithm")

    assert [v.arcade_id for v in visits] == [arcade.id]
