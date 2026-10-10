# skills/account-link/models.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import aiosqlite


@dataclass(slots=True, kw_only=True)
class LinkCode:
    code: str
    issuer_player_id: str
    issuer_discord_id: str
    joiner_discord_id: str | None
    status: str
    issuer_confirmed: bool
    joiner_confirmed: bool
    expires_at: str


def _row_to_code(row: aiosqlite.Row) -> LinkCode:
    return LinkCode(
        code=row["code"],
        issuer_player_id=row["issuer_player_id"],
        issuer_discord_id=row["issuer_discord_id"],
        joiner_discord_id=row["joiner_discord_id"],
        status=row["status"],
        issuer_confirmed=bool(row["issuer_confirmed"]),
        joiner_confirmed=bool(row["joiner_confirmed"]),
        expires_at=row["expires_at"],
    )


async def create_code(
    conn: aiosqlite.Connection, *, code: str, issuer_player_id: str, issuer_discord_id: str,
    now: datetime, ttl: timedelta,
) -> None:
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        "INSERT INTO account_link_codes "
        "(code, issuer_player_id, issuer_discord_id, status, created_at, expires_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?)",
        (code, issuer_player_id, issuer_discord_id, now.isoformat(), (now + ttl).isoformat()),
    )
    await conn.commit()


async def get_code(conn: aiosqlite.Connection, code: str) -> LinkCode | None:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute("SELECT * FROM account_link_codes WHERE code = ?", (code,))
    row = await cursor.fetchone()
    return _row_to_code(row) if row else None


async def mark_joined(conn: aiosqlite.Connection, code: str, *, joiner_discord_id: str) -> None:
    await conn.execute(
        "UPDATE account_link_codes SET joiner_discord_id = ?, status = 'joined' WHERE code = ?",
        (joiner_discord_id, code),
    )
    await conn.commit()


async def set_confirmed(conn: aiosqlite.Connection, code: str, *, side: str) -> None:
    column = "issuer_confirmed" if side == "issuer" else "joiner_confirmed"
    await conn.execute(f"UPDATE account_link_codes SET {column} = 1 WHERE code = ?", (code,))
    await conn.commit()


async def mark_status(conn: aiosqlite.Connection, code: str, *, status: str) -> None:
    await conn.execute("UPDATE account_link_codes SET status = ? WHERE code = ?", (status, code))
    await conn.commit()


async def wrong_attempt_count(conn: aiosqlite.Connection, discord_id: str, *, now: datetime) -> int:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        "SELECT wrong_count, locked_until FROM account_link_attempts WHERE discord_id = ?", (discord_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        return 0
    if row["locked_until"] and row["locked_until"] > now.isoformat():
        return 999  # 잠겨 있는 동안은 횟수와 무관하게 막힌 것으로 본다
    return row["wrong_count"]


async def record_wrong_attempt(
    conn: aiosqlite.Connection, discord_id: str, *, now: datetime, lock_after: int, cooldown: timedelta
) -> int:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        "SELECT wrong_count FROM account_link_attempts WHERE discord_id = ?", (discord_id,)
    )
    row = await cursor.fetchone()
    count = (row["wrong_count"] if row else 0) + 1
    locked_until = (now + cooldown).isoformat() if count >= lock_after else None
    await conn.execute(
        "INSERT INTO account_link_attempts (discord_id, wrong_count, locked_until) VALUES (?, ?, ?) "
        "ON CONFLICT (discord_id) DO UPDATE SET wrong_count = excluded.wrong_count, "
        "locked_until = excluded.locked_until",
        (discord_id, count, locked_until),
    )
    await conn.commit()
    return count


async def reset_wrong_attempts(conn: aiosqlite.Connection, discord_id: str) -> None:
    await conn.execute(
        "INSERT INTO account_link_attempts (discord_id, wrong_count, locked_until) VALUES (?, 0, NULL) "
        "ON CONFLICT (discord_id) DO UPDATE SET wrong_count = 0, locked_until = NULL",
        (discord_id,),
    )
    await conn.commit()
