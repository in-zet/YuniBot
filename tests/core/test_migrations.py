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

        up_to_023 = tmp_path / "core_023"
        up_to_023.mkdir()
        for sql in sorted(real_core.glob("*.sql")):
            if sql.name < "024":
                (up_to_023 / sql.name).write_text(sql.read_text())
        applied = await run_migrations(conn, roots=[("core", up_to_023)])
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


@pytest.mark.asyncio
async def test_024_to_027_add_chart_type_game_columns_and_rename_cookies_keeping_rows(tmp_path: Path) -> None:
    real_core = Path("migrations/core")
    skill_dirs = {name: Path("skills") / name / "migrations" for name in ("folders", "notes", "yuni-bond")}

    def copy_before(source: Path, target: Path, first_new: str) -> Path:
        target.mkdir()
        for sql in sorted(source.glob("*.sql")):
            if sql.name < first_new:
                (target / sql.name).write_text(sql.read_text())
        return target

    old_roots = [("core", copy_before(real_core, tmp_path / "core", "024"))]
    old_roots += [(name, copy_before(path, tmp_path / name, "002")) for name, path in skill_dirs.items()]

    async with aiosqlite.connect(":memory:") as conn:
        await run_migrations(conn, roots=old_roots)
        await conn.executescript(
            """
            INSERT INTO players (id, first_seen_at) VALUES ('p1', 't');
            INSERT INTO records_sessions (id, player_id, play_date, created_at) VALUES ('s1', 'p1', '2026-10-23', 't');
            INSERT INTO records_pb (player_id, song_title, difficulty, score, rank, clear_lamp, combo_lamp,
                chain_lamp, const, rating, session_id, updated_at)
            VALUES ('p1', 'Song', 'MAS', 1009000, 'SSS+', 'CLEAR', 'AJ', 'NONE', 14.5, '16.65', 's1', 't');
            INSERT INTO songdb_aliases (alias, song_title, added_by, created_at) VALUES ('sg', 'Song', 'p1', 't');
            INSERT INTO chunithm_net_cookies (player_id, encrypted_cookie, linked_at, nickname)
            VALUES ('p1', x'00ff', 't', 'nick');
            INSERT INTO folders_folders (id, player_id, name, created_at, updated_at) VALUES (1, 'p1', 'f', 't', 't');
            INSERT INTO folders_charts (folder_id, song_title, difficulty, added_at) VALUES (1, 'Song', 'MAS', 't');
            INSERT INTO notes_notes (player_id, song_title, difficulty, body, updated_at)
            VALUES ('p1', 'Song', 'MAS', 'memo', 't');
            INSERT INTO yuni_bond_milestones (player_id, kind, song_title, difficulty, value, occurred_on)
            VALUES ('p1', 'first_sss', 'Song', 'MAS', 'SSS', '2026-10-23');
            """
        )
        await conn.commit()

        real_roots = [("core", real_core)] + list(skill_dirs.items())
        applied = await run_migrations(conn, roots=real_roots)
        assert applied == [
            "core/024_records_chart_type.sql",
            "core/025_songdb_aliases_game.sql",
            "core/026_sega_cookies.sql",
            "core/027_players_main_game.sql",
            "folders/002_chart_type.sql",
            "notes/002_chart_type.sql",
            "yuni-bond/002_chart_type.sql",
        ]

        async def row(sql: str) -> tuple[object, ...]:
            cursor = await conn.execute(sql)
            fetched = await cursor.fetchone()
            assert fetched is not None
            return tuple(fetched)

        assert await row("SELECT chart_type, dx_score, score, session_id FROM records_pb") == ("", None, 1009000, "s1")
        assert await row("SELECT game, alias, song_title FROM songdb_aliases") == ("chunithm", "sg", "Song")
        assert await row("SELECT encrypted_cookie, nickname FROM sega_cookies") == (b"\x00\xff", "nick")
        assert await row("SELECT main_game FROM players") == ("",)
        assert await row("SELECT chart_type, difficulty FROM folders_charts") == ("", "MAS")
        assert await row("SELECT chart_type, difficulty, body FROM notes_notes") == ("", "MAS", "memo")
        assert await row("SELECT chart_type, kind, value FROM yuni_bond_milestones") == ("", "first_sss", "SSS")

        # 새 기본 키: 같은 곡·난이도라도 chart_type이 다르면 별개 채보다(STD/DX).
        await conn.execute(
            "INSERT INTO records_pb (player_id, song_title, chart_type, difficulty, score, rank, clear_lamp, "
            "combo_lamp, chain_lamp, rating, updated_at) VALUES ('p1', 'Song', 'DX', 'MAS', 1, 'D', 'CLEAR', "
            "'NONE', 'NONE', '0', 't')"
        )
        assert await row("SELECT COUNT(*) FROM records_pb") == (2,)
        await conn.execute(
            "INSERT INTO songdb_aliases (game, alias, song_title, added_by, created_at) "
            "VALUES ('maimai', 'sg', 'Other', 'p1', 't')"
        )
        assert await row("SELECT COUNT(*) FROM songdb_aliases") == (2,)
