from pathlib import Path

import aiosqlite
import pytest

from core.migrations import MigrationError, run_migrations


@pytest.mark.asyncio
async def test_run_migrations_applies_in_order_and_records(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "001_init.sql").write_text("CREATE TABLE players (id TEXT PRIMARY KEY);")

    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "001_init.sql").write_text("CREATE TABLE demo_notes (id INTEGER PRIMARY KEY);")

    async with aiosqlite.connect(":memory:") as conn:
        applied = await run_migrations(conn, roots=[("core", core_dir), ("demo", skill_dir)])
        assert applied == ["core/001_init.sql", "demo/001_init.sql"]

        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}
        assert {"players", "demo_notes", "schema_migrations"} <= tables


@pytest.mark.asyncio
async def test_run_migrations_is_idempotent(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "001_init.sql").write_text("CREATE TABLE players (id TEXT PRIMARY KEY);")

    async with aiosqlite.connect(":memory:") as conn:
        first = await run_migrations(conn, roots=[("core", core_dir)])
        second = await run_migrations(conn, roots=[("core", core_dir)])
        assert first == ["core/001_init.sql"]
        assert second == []


@pytest.mark.asyncio
async def test_run_migrations_rolls_back_failed_file(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "001_bad.sql").write_text(
        "CREATE TABLE ok_table (id INTEGER); THIS IS NOT SQL;"
    )

    async with aiosqlite.connect(":memory:") as conn:
        with pytest.raises(MigrationError):
            await run_migrations(conn, roots=[("core", core_dir)])

        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}
        assert "ok_table" not in tables


@pytest.mark.asyncio
async def test_run_migrations_applies_multi_statement_file_with_semicolon_in_string(
    tmp_path: Path,
) -> None:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "001_init.sql").write_text(
        "CREATE TABLE players (id TEXT PRIMARY KEY, note TEXT DEFAULT 'a;b');\n"
        + "CREATE INDEX idx_players_note ON players(note);\n"
        + "INSERT INTO players (id) VALUES ('p1'); -- trailing comment with ; inside\n"
    )

    async with aiosqlite.connect(":memory:") as conn:
        applied = await run_migrations(conn, roots=[("core", core_dir)])
        assert applied == ["core/001_init.sql"]

        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}
        assert {"players"} <= tables

        cursor = await conn.execute("SELECT id, note FROM players")
        rows = await cursor.fetchall()
        assert rows == [("p1", "a;b")]


@pytest.mark.asyncio
async def test_run_migrations_detects_changed_applied_file(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    migration_file = core_dir / "001_init.sql"
    migration_file.write_text("CREATE TABLE players (id TEXT PRIMARY KEY);")

    async with aiosqlite.connect(":memory:") as conn:
        await run_migrations(conn, roots=[("core", core_dir)])

        migration_file.write_text("CREATE TABLE players (id TEXT PRIMARY KEY, nickname TEXT);")

        with pytest.raises(MigrationError, match="바뀌었다"):
            await run_migrations(conn, roots=[("core", core_dir)])
