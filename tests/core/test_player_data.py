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


async def test_llm_usage_is_never_discovered_or_deleted(conn: aiosqlite.Connection) -> None:
    await conn.execute("CREATE TABLE llm_usage (id INTEGER PRIMARY KEY, player_id TEXT, estimated_cost_usd REAL)")
    await conn.execute("INSERT INTO llm_usage VALUES (1, 'p1', 0.5)")
    tables = await discover_skill_tables(conn)
    assert "llm_usage" not in tables

    await PlayerDataScanner(conn=conn, table_names=tables).delete("p1")
    cursor = await conn.execute("SELECT COUNT(*) FROM llm_usage")
    row = await cursor.fetchone()
    assert row is not None and row[0] == 1


async def test_scanner_delete_follows_foreign_keys_to_child_tables_without_player_id(
    conn: aiosqlite.Connection,
) -> None:
    await conn.executescript(
        """
        CREATE TABLE folders_x (id INTEGER PRIMARY KEY, player_id TEXT);
        CREATE TABLE folders_x_charts (folder_id INTEGER REFERENCES folders_x(id), title TEXT);
        CREATE TABLE folders_x_notes (chart_rowid INTEGER REFERENCES folders_x_charts(rowid), body TEXT);
        INSERT INTO folders_x VALUES (1, 'p1'), (2, 'p2');
        INSERT INTO folders_x_charts VALUES (1, 'a'), (2, 'b');
        INSERT INTO folders_x_notes VALUES (1, 'n1'), (2, 'n2');
        """
    )
    tables = await discover_skill_tables(conn)
    assert "folders_x_charts" not in tables

    await PlayerDataScanner(conn=conn, table_names=tables).delete("p1")

    cursor = await conn.execute("SELECT title FROM folders_x_charts")
    assert [row[0] for row in await cursor.fetchall()] == ["b"]
    cursor = await conn.execute("SELECT body FROM folders_x_notes")
    assert [row[0] for row in await cursor.fetchall()] == ["n2"]
