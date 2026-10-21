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
    Path("migrations/core/011_songdb_aliases.sql").read_text(),
    Path("migrations/core/020_aliases_global.sql").read_text(),
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


_CTX = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
_DM_CTX = ToolContext(player_id="p2", tags=frozenset(), services={}, guild_id=None)


async def test_create_and_list_all(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(name="동인천 로지텍", created_by="p1")

    assert [a.name for a in await service.list_all()] == ["동인천 로지텍"]
    assert arcade.created_by == "p1"


async def test_create_duplicate_name_fails_ignoring_spaces_and_case(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    await service.create(name="Gwangju GamePlaza", created_by="p1")

    with pytest.raises(ValueError, match="이미"):
        await service.create(name="gwangju game plaza", created_by="p2")


async def test_resolve_exact_name_match_ignores_spaces(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(name="광주 게임플라자", created_by="p1")

    assert (await service.resolve("광주 게임플라자", _CTX)).id == arcade.id
    assert (await service.resolve("광주게임플라자", _CTX)).id == arcade.id


async def test_alias_is_global_for_every_player_guild_and_dm(conn: aiosqlite.Connection) -> None:
    """별칭은 봇 전역이다(2026-10-21): 등록한 사람·서버와 상관없이, 서버 밖(DM)에서도 풀린다."""
    service = ArcadesService(conn)
    arcade = await service.create(name="동인천 로지텍", created_by="p1")
    await service.add_alias(arcade.id, player_id="p1", alias="동로")

    assert (await service.resolve("동로", _CTX)).id == arcade.id
    assert (await service.resolve("동로", _DM_CTX)).id == arcade.id


async def test_alias_already_used_by_another_arcade_fails(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    first = await service.create(name="동인천 로지텍", created_by="p1")
    second = await service.create(name="부산 오락실", created_by="p1")
    await service.add_alias(first.id, player_id="p1", alias="단골집")

    with pytest.raises(ValueError, match="이미"):
        await service.add_alias(second.id, player_id="p2", alias="단골집")
    await service.add_alias(first.id, player_id="p2", alias="단골집")  # 같은 오락실이면 그대로 둔다


async def test_alias_equal_to_another_arcades_name_fails(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    first = await service.create(name="동인천 로지텍", created_by="p1")
    await service.create(name="부산 오락실", created_by="p1")

    with pytest.raises(ValueError, match="이름"):
        await service.add_alias(first.id, player_id="p1", alias="부산 오락실")


async def test_resolve_ambiguous_returns_candidates(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    await service.create(name="동인천 로지텍", created_by="p1")
    await service.create(name="동인천 아이플러스", created_by="p1")

    with pytest.raises(ResolutionError) as exc_info:
        await service.resolve("동인천", _CTX)
    assert set(exc_info.value.candidates) == {"동인천 로지텍", "동인천 아이플러스"}


async def test_resolve_suggests_the_closest_name_for_a_typo(conn: aiosqlite.Connection) -> None:
    """글자가 조금 다르면 비슷한 이름을 후보로 준다(모델이 "이거 맞아?"하고 되묻는다)."""
    service = ArcadesService(conn)
    await service.create(name="광주 게임플라자", created_by="p1")

    with pytest.raises(ResolutionError) as exc_info:
        await service.resolve("광주 겜플라자", _CTX)
    assert exc_info.value.candidates == ["광주 게임플라자"]


async def test_resolve_suggests_via_alias_owner_without_duplicates(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(name="광주 게임플라자", created_by="p1")
    await service.add_alias(arcade.id, player_id="p1", alias="광주 겜플")

    with pytest.raises(ResolutionError) as exc_info:
        await service.resolve("광주겜", _CTX)
    assert exc_info.value.candidates == ["광주 게임플라자"]


async def test_resolve_no_match_raises_not_found(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)

    with pytest.raises(ResolutionError, match="등록돼 있지 않음"):
        await service.resolve("없는오락실", _CTX)


async def test_resolve_works_outside_a_guild(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(name="동인천 로지텍", created_by="p1")

    assert (await service.resolve("동인천 로지텍", _DM_CTX)).id == arcade.id


async def test_record_and_list_visits(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(name="동인천 로지텍", created_by="p1")

    await service.record_visit(arcade.id, player_id="p1", game="chunithm")
    visits = await service.visits_for(player_id="p1", game="chunithm")

    assert [v.arcade_id for v in visits] == [arcade.id]
