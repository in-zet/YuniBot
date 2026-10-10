# services/common/players.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

_VISIBILITY_FIELDS = frozenset(
    {
        "records_public",
        "memos_public",
        "goals_public",
        "arcade_public",
        "reviews_public",
        "sync_report_public",
    }
)


@dataclass(slots=True, kw_only=True)
class Player:
    id: str
    primary_discord_id: str | None
    records_public: bool
    memos_public: bool
    goals_public: bool
    arcade_public: bool
    reviews_public: bool
    sync_report_public: bool
    first_seen_at: str


@dataclass(slots=True, kw_only=True)
class DiscordAccount:
    discord_id: str
    player_id: str
    is_primary: bool
    linked_at: str


def _row_to_player(row: aiosqlite.Row) -> Player:
    return Player(
        id=row["id"],
        primary_discord_id=row["primary_discord_id"],
        records_public=bool(row["records_public"]),
        memos_public=bool(row["memos_public"]),
        goals_public=bool(row["goals_public"]),
        arcade_public=bool(row["arcade_public"]),
        reviews_public=bool(row["reviews_public"]),
        sync_report_public=bool(row["sync_report_public"]),
        first_seen_at=row["first_seen_at"],
    )


class PlayersService:
    """플레이어, 디스코드 계정 연결, 공개 설정을 다룬다. 태그는 Task 4에서 더한다."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row

    async def create_player(self) -> Player:
        player_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO players (id, primary_discord_id, first_seen_at) VALUES (?, NULL, ?)",
            (player_id, now),
        )
        await self._conn.commit()
        player = await self.get_player(player_id)
        assert player is not None
        return player

    async def get_player(self, player_id: str) -> Player | None:
        cursor = await self._conn.execute("SELECT * FROM players WHERE id = ?", (player_id,))
        row = await cursor.fetchone()
        return _row_to_player(row) if row else None

    async def player_for_discord(self, discord_id: str) -> Player | None:
        cursor = await self._conn.execute(
            "SELECT p.* FROM players p JOIN player_discord_accounts a ON a.player_id = p.id "
            "WHERE a.discord_id = ?",
            (discord_id,),
        )
        row = await cursor.fetchone()
        return _row_to_player(row) if row else None

    async def accounts_for(self, player_id: str) -> list[DiscordAccount]:
        cursor = await self._conn.execute(
            "SELECT a.discord_id, a.player_id, a.linked_at, p.primary_discord_id "
            "FROM player_discord_accounts a JOIN players p ON p.id = a.player_id "
            "WHERE a.player_id = ? ORDER BY a.linked_at",
            (player_id,),
        )
        rows = await cursor.fetchall()
        return [
            DiscordAccount(
                discord_id=row["discord_id"],
                player_id=row["player_id"],
                is_primary=row["discord_id"] == row["primary_discord_id"],
                linked_at=row["linked_at"],
            )
            for row in rows
        ]

    async def link_account(self, player_id: str, discord_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO player_discord_accounts (discord_id, player_id, linked_at) VALUES (?, ?, ?)",
            (discord_id, player_id, now),
        )
        cursor = await self._conn.execute(
            "SELECT primary_discord_id FROM players WHERE id = ?", (player_id,)
        )
        row = await cursor.fetchone()
        if row is not None and row["primary_discord_id"] is None:
            await self._conn.execute(
                "UPDATE players SET primary_discord_id = ? WHERE id = ?", (discord_id, player_id)
            )
        await self._conn.commit()

    async def unlink_account(self, discord_id: str) -> None:
        cursor = await self._conn.execute(
            "SELECT player_id FROM player_discord_accounts WHERE discord_id = ?", (discord_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return
        player_id = row["player_id"]

        await self._conn.execute(
            "DELETE FROM player_discord_accounts WHERE discord_id = ?", (discord_id,)
        )

        cursor = await self._conn.execute(
            "SELECT primary_discord_id FROM players WHERE id = ?", (player_id,)
        )
        prow = await cursor.fetchone()
        if prow is not None and prow["primary_discord_id"] == discord_id:
            remaining = await self._conn.execute(
                "SELECT discord_id FROM player_discord_accounts WHERE player_id = ? "
                "ORDER BY linked_at LIMIT 1",
                (player_id,),
            )
            next_row = await remaining.fetchone()
            new_primary = next_row["discord_id"] if next_row else None
            await self._conn.execute(
                "UPDATE players SET primary_discord_id = ? WHERE id = ?", (new_primary, player_id)
            )
        await self._conn.commit()

    async def set_primary(self, player_id: str, discord_id: str) -> None:
        cursor = await self._conn.execute(
            "SELECT 1 FROM player_discord_accounts WHERE player_id = ? AND discord_id = ?",
            (player_id, discord_id),
        )
        if await cursor.fetchone() is None:
            raise ValueError(f"'{discord_id}'는 이 플레이어에 연결되지 않은 계정이다")
        await self._conn.execute(
            "UPDATE players SET primary_discord_id = ? WHERE id = ?", (discord_id, player_id)
        )
        await self._conn.commit()

    async def set_visibility(self, player_id: str, field: str, value: bool) -> None:
        if field not in _VISIBILITY_FIELDS:
            raise ValueError(f"'{field}'는 공개 설정 필드가 아니다: {sorted(_VISIBILITY_FIELDS)}")
        await self._conn.execute(
            f"UPDATE players SET {field} = ? WHERE id = ?", (1 if value else 0, player_id)
        )
        await self._conn.commit()
