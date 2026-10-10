# tests/services/test_settings.py
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from services.common.settings import SettingsService

_MIGRATION = Path("migrations/core/003_settings.sql").read_text()


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_get_value_returns_default_when_missing(conn: aiosqlite.Connection) -> None:
    service = SettingsService(conn)

    value = await service.get_value(scope="player", scope_id="p1", skill="yuni-bond", name="hint", default=True)

    assert value is True


async def test_set_then_get_value_round_trips_json_scalars(conn: aiosqlite.Connection) -> None:
    service = SettingsService(conn)

    await service.set_value(
        scope="player", scope_id="p1", skill="yuni-bond", name="hint", value=False, updated_by="p1"
    )
    value = await service.get_value(scope="player", scope_id="p1", skill="yuni-bond", name="hint", default=True)

    assert value is False


async def test_set_value_is_scoped_per_player(conn: aiosqlite.Connection) -> None:
    service = SettingsService(conn)

    await service.set_value(
        scope="player", scope_id="p1", skill="yuni-bond", name="hint", value=False, updated_by="p1"
    )

    other = await service.get_value(scope="player", scope_id="p2", skill="yuni-bond", name="hint", default=True)
    assert other is True


async def test_skill_enabled_defaults_to_true(conn: aiosqlite.Connection) -> None:
    service = SettingsService(conn)

    assert await service.is_skill_enabled(guild_id="g1", skill="web-qa") is True


async def test_set_skill_enabled_is_scoped_per_guild(conn: aiosqlite.Connection) -> None:
    service = SettingsService(conn)

    await service.set_skill_enabled(guild_id="g1", skill="web-qa", enabled=False, updated_by="admin1")

    assert await service.is_skill_enabled(guild_id="g1", skill="web-qa") is False
    assert await service.is_skill_enabled(guild_id="g2", skill="web-qa") is True
