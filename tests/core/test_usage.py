from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core.migrations import run_migrations
from core.usage import UsageService


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await run_migrations(connection, roots=[("core", Path("migrations/core"))])
    try:
        yield connection
    finally:
        await connection.close()


async def test_record_returns_estimated_cost(conn: aiosqlite.Connection) -> None:
    service = UsageService(conn)
    cost = await service.record(
        guild_id="g1", player_id="p1", model="gemini-3.8-flash", kind="agent",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert cost == 0.75 + 3.75


async def test_month_total_usd_sums_only_that_guild_and_month(conn: aiosqlite.Connection) -> None:
    service = UsageService(conn)
    await service.record(guild_id="g1", player_id="p1", model="gemini-3.8-flash", kind="agent", input_tokens=1_000_000, output_tokens=0)
    await service.record(guild_id="g2", player_id="p1", model="gemini-3.8-flash", kind="agent", input_tokens=1_000_000, output_tokens=0)

    cursor = await conn.execute("SELECT created_at FROM llm_usage WHERE guild_id = 'g1'")
    row = await cursor.fetchone()
    assert row is not None
    year_month = row[0][:7]

    total = await service.month_total_usd(guild_id="g1", year_month=year_month)
    assert total == pytest.approx(0.75)


async def test_month_summary_groups_by_player(conn: aiosqlite.Connection) -> None:
    service = UsageService(conn)
    await service.record(guild_id="g1", player_id="p1", model="gemini-3.8-flash", kind="agent", input_tokens=1_000_000, output_tokens=0)
    await service.record(guild_id="g1", player_id="p2", model="gemini-3.8-flash", kind="agent", input_tokens=1_000_000, output_tokens=0)

    cursor = await conn.execute("SELECT created_at FROM llm_usage LIMIT 1")
    row = await cursor.fetchone()
    assert row is not None
    year_month = row[0][:7]

    summary = await service.month_summary(guild_id="g1", year_month=year_month)
    assert summary.calls == 2
    assert summary.by_player["p1"] == pytest.approx(0.75)
    assert summary.by_player["p2"] == pytest.approx(0.75)
    assert summary.total_usd == pytest.approx(1.5)
    assert summary.total_input_tokens == 2_000_000
