from collections.abc import AsyncIterator

import aiosqlite
import pytest

from core.player_data import PlayerDataScanner, discover_skill_tables


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(
        """
        CREATE TABLE players (id TEXT PRIMARY KEY, primary_discord_id TEXT, first_seen_at TEXT);
        CREATE TABLE player_tags (player_id TEXT, tag TEXT, source TEXT, granted_at TEXT);
        CREATE TABLE goals_items (id TEXT PRIMARY KEY, player_id TEXT, text TEXT);
        CREATE TABLE goals_history (id TEXT PRIMARY KEY, player_id TEXT, at TEXT);
        INSERT INTO goals_items VALUES ('g1', 'p1', '목표 1');
        INSERT INTO goals_items VALUES ('g2', 'p1', '목표 2');
        INSERT INTO goals_items VALUES ('g3', 'p2', '목표 3');
        INSERT INTO goals_history VALUES ('h1', 'p1', 'now');
        """
    )
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_discover_skill_tables_excludes_core_tables(conn: aiosqlite.Connection) -> None:
    tables = await discover_skill_tables(conn)
    assert tables == ("goals_history", "goals_items")


async def test_scanner_counts_only_matching_player(conn: aiosqlite.Connection) -> None:
    tables = await discover_skill_tables(conn)
    scanner = PlayerDataScanner(conn=conn, table_names=tables)

    counts = await scanner.count("p1")
    assert counts == {"goals_items": 2, "goals_history": 1}

    counts_p2 = await scanner.count("p2")
    assert counts_p2 == {"goals_items": 1}


async def test_scanner_delete_removes_rows_and_returns_prior_counts(conn: aiosqlite.Connection) -> None:
    tables = await discover_skill_tables(conn)
    scanner = PlayerDataScanner(conn=conn, table_names=tables)

    deleted = await scanner.delete("p1")
    assert deleted == {"goals_items": 2, "goals_history": 1}

    cursor = await conn.execute("SELECT player_id FROM goals_items")
    remaining = {row[0] for row in await cursor.fetchall()}
    assert remaining == {"p2"}
    assert await scanner.count("p1") == {}
