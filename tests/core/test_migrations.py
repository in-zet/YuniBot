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


@pytest.mark.asyncio
async def test_022_023_move_stored_skill_names_and_login_tag_to_new_names(tmp_path: Path) -> None:
    real_core = Path("migrations/core")
    old_dir = tmp_path / "core_old"
    old_dir.mkdir()
    for sql in sorted(real_core.glob("*.sql")):
        if sql.name < "022":
            (old_dir / sql.name).write_text(sql.read_text())

    async with aiosqlite.connect(":memory:") as conn:
        await run_migrations(conn, roots=[("core", old_dir)])
        await conn.execute(
            "INSERT INTO schema_migrations (owner, filename, checksum, applied_at) VALUES "
            "('chunithm-sync', '001_init.sql', 'x', 't'), ('memory', '001_init.sql', 'x', 't')"
        )
        await conn.execute(
            "INSERT INTO settings_skill_toggles (guild_id, skill, enabled, updated_by, updated_at) VALUES "
            "('g', 'chunithm-account', 0, 'u', 't'), ('g', 'memory', 0, 'u', 't')"
        )
        await conn.execute(
            "INSERT INTO settings_values (scope, scope_id, skill, name, value, updated_at) "
            "VALUES ('guild', 'g', 'chunithm-sync', 'cooldown', '60', 't')"
        )
        await conn.execute(
            "INSERT INTO schedule_recurring (owner_skill, fn_name, cron, catch_up, next_run_at) "
            "VALUES ('chunithm-updates', 'check', '* * * * *', 1, 't')"
        )
        await conn.execute(
            "INSERT INTO schedule_once (owner_skill, event_name, payload, fire_at, catch_up) "
            "VALUES ('chunithm-goals', 'e', '{}', 't', 0)"
        )
        await conn.execute(
            "INSERT INTO conversation_skills (message_id, skills, created_at) "
            '''VALUES ('m', '["chunithm-song", "memory", "chunithm-sync"]', 0)'''
        )
        await conn.execute("INSERT INTO players (id, first_seen_at) VALUES ('p1', 't')")
        await conn.execute(
            "INSERT INTO player_tags (player_id, tag, source, granted_at) VALUES "
            "('p1', 'chunithm-linked', 'login', 't'), ('p1', 'chunithm-synced', 'sync', 't')"
        )
        await conn.commit()

        applied = await run_migrations(conn, roots=[("core", real_core)])
        assert applied == ["core/022_rename_skills.sql", "core/023_rename_login_tag.sql"]

        async def column(sql: str) -> list[str]:
            cursor = await conn.execute(sql)
            return sorted(row[0] for row in await cursor.fetchall())

        assert await column("SELECT owner FROM schema_migrations WHERE filename = '001_init.sql'") == ["memory", "sync"]
        assert await column("SELECT skill FROM settings_skill_toggles") == ["memory", "sega-account"]
        assert await column("SELECT skill FROM settings_values") == ["sync"]
        assert await column("SELECT owner_skill FROM schedule_recurring") == ["updates"]
        assert await column("SELECT owner_skill FROM schedule_once") == ["goals"]
        assert await column("SELECT skills FROM conversation_skills") == ['["song", "memory", "sync"]']
        assert await column("SELECT tag FROM player_tags") == ["chunithm-synced", "sega-linked"]
