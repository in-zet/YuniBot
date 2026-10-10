# services/common/arcades.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from core.resolvers import ResolutionError
from core.tool_types import ToolContext


@dataclass(slots=True, kw_only=True)
class Arcade:
    id: str
    guild_id: str
    name: str
    created_by: str
    created_at: str


@dataclass(slots=True, kw_only=True)
class ArcadeVisit:
    arcade_id: str
    player_id: str
    game: str
    visited_at: str


def _row_to_arcade(row: aiosqlite.Row) -> Arcade:
    return Arcade(
        id=row["id"], guild_id=row["guild_id"], name=row["name"],
        created_by=row["created_by"], created_at=row["created_at"],
    )


class ArcadesService:
    """서버 공유 오락실 목록, 플레이어별 별칭, 이름 확인, 게임별 방문 기록."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row

    async def create(self, *, guild_id: str, name: str, created_by: str) -> Arcade:
        existing = await self._conn.execute(
            "SELECT 1 FROM arcades WHERE guild_id = ? AND name = ?", (guild_id, name)
        )
        if await existing.fetchone() is not None:
            raise ValueError(f"'{name}'은 이미 이 서버에 등록된 오락실이다")

        arcade_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO arcades (id, guild_id, name, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (arcade_id, guild_id, name, created_by, now),
        )
        await self._conn.commit()
        return Arcade(id=arcade_id, guild_id=guild_id, name=name, created_by=created_by, created_at=now)

    async def list_for_guild(self, guild_id: str) -> list[Arcade]:
        cursor = await self._conn.execute(
            "SELECT * FROM arcades WHERE guild_id = ? ORDER BY name", (guild_id,)
        )
        rows = await cursor.fetchall()
        return [_row_to_arcade(row) for row in rows]

    async def add_alias(self, arcade_id: str, *, player_id: str, alias: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO arcade_aliases (arcade_id, player_id, alias, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (player_id, alias) DO UPDATE SET arcade_id = excluded.arcade_id",
            (arcade_id, player_id, alias, now),
        )
        await self._conn.commit()

    async def record_visit(self, arcade_id: str, *, player_id: str, game: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO arcade_visits (arcade_id, player_id, game, visited_at) VALUES (?, ?, ?, ?)",
            (arcade_id, player_id, game, now),
        )
        await self._conn.commit()

    async def visits_for(self, *, player_id: str, game: str) -> list[ArcadeVisit]:
        cursor = await self._conn.execute(
            "SELECT * FROM arcade_visits WHERE player_id = ? AND game = ? ORDER BY visited_at",
            (player_id, game),
        )
        rows = await cursor.fetchall()
        return [
            ArcadeVisit(
                arcade_id=row["arcade_id"], player_id=row["player_id"], game=row["game"],
                visited_at=row["visited_at"],
            )
            for row in rows
        ]

    async def resolve(self, raw: str, ctx: ToolContext) -> Arcade:
        """ArcadeRef 해석기. 새 오락실/별칭은 여기서 만들지 않는다(버튼으로 확정한 뒤에만
        저장한다 — 그건 chunithm-arcade 스킬의 mutates 도구 몫이다)."""
        if ctx.guild_id is None:
            raise ResolutionError(message="서버 밖에서는 오락실을 찾을 수 없다")
        text = raw.strip()

        cursor = await self._conn.execute(
            "SELECT * FROM arcades WHERE guild_id = ? AND name = ?", (ctx.guild_id, text)
        )
        exact = await cursor.fetchone()
        if exact is not None:
            return _row_to_arcade(exact)

        alias_cursor = await self._conn.execute(
            "SELECT a.* FROM arcades a JOIN arcade_aliases al ON al.arcade_id = a.id "
            "WHERE a.guild_id = ? AND al.player_id = ? AND al.alias = ?",
            (ctx.guild_id, ctx.player_id, text),
        )
        alias_hit = await alias_cursor.fetchone()
        if alias_hit is not None:
            return _row_to_arcade(alias_hit)

        candidates_cursor = await self._conn.execute(
            "SELECT name FROM arcades WHERE guild_id = ? AND name LIKE ?", (ctx.guild_id, f"%{text}%")
        )
        candidates = [row["name"] for row in await candidates_cursor.fetchall()]
        if candidates:
            raise ResolutionError(candidates=candidates)

        raise ResolutionError(message="그런 오락실은 이 서버에 없음")
