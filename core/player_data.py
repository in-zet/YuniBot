from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

# players/settings/memory/arcades/schedule 등 코어 자신의 테이블. "모든 스킬 테이블"에서
# 뺀다 — 이들은 각자의 서비스(players.unlink_account, memory.clear 등)가 다룬다.
CORE_OWNED_TABLES: frozenset[str] = frozenset(
    {
        "schema_migrations",
        "players",
        "player_discord_accounts",
        "player_tags",
        "settings_values",
        "settings_skill_toggles",
        "player_memory",
        "arcades",
        "arcade_aliases",
        "arcade_visits",
        "schedule_recurring",
        "schedule_once",
    }
)


async def discover_skill_tables(conn: aiosqlite.Connection) -> tuple[str, ...]:
    """player_id 컬럼이 있는 테이블 중 코어 자신의 테이블을 뺀 것 — "모든 스킬 테이블"이다.

    스킬 이름 목록을 레지스트리에서 받는 대신 실제 DB 스키마를 직접 본다. 그래서 스킬이
    늘어도(또는 테스트 픽스처가 임시 테이블을 더해도) 이 함수를 고칠 필요가 없다.
    """
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    rows = await cursor.fetchall()
    tables: list[str] = []
    for row in rows:
        name = row[0]
        if name in CORE_OWNED_TABLES:
            continue
        info_cursor = await conn.execute(f'PRAGMA table_info("{name}")')
        columns = await info_cursor.fetchall()
        if any(col[1] == "player_id" for col in columns):
            tables.append(name)
    return tuple(sorted(tables))


@dataclass(slots=True, kw_only=True)
class PlayerDataScanner:
    """계정 연결에서 "버릴 데이터"를 세고 지운다. core/bot.py가 기동 시 한 번 만들어
    ToolContext에 실어 스킬(주로 account-link)에 넘긴다."""

    conn: aiosqlite.Connection
    table_names: tuple[str, ...]

    async def count(self, player_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in self.table_names:
            cursor = await self.conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE player_id = ?', (player_id,)
            )
            row = await cursor.fetchone()
            count = row[0] if row else 0
            if count:
                counts[table] = count
        return counts

    async def delete(self, player_id: str) -> dict[str, int]:
        counts = await self.count(player_id)
        for table in counts:
            await self.conn.execute(f'DELETE FROM "{table}" WHERE player_id = ?', (player_id,))
        await self.conn.commit()
        return counts
