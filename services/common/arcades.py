# services/common/arcades.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from core.fuzzy import normalize_name, similar_names
from core.resolvers import ResolutionError
from core.tool_types import ToolContext


@dataclass(slots=True, kw_only=True)
class Arcade:
    id: str
    name: str
    created_by: str
    created_at: str


@dataclass(slots=True, kw_only=True)
class ArcadeVisit:
    arcade_id: str
    player_id: str
    game: str
    visited_at: str
    # arcade_visits에는 따로 PK가 없어 SQLite의 암묵적 rowid를 쓴다(record_visit()이 그대로
    # 돌려주는 값). 기본값 0은 기존 호출부(테스트 더미)가 깨지지 않게 하기 위함 — 실제 저장된
    # 행은 항상 양수 rowid를 받는다.
    id: int = 0


def _row_to_arcade(row: aiosqlite.Row) -> Arcade:
    return Arcade(id=row["id"], name=row["name"], created_by=row["created_by"], created_at=row["created_at"])


class ArcadesService:
    """봇 전역 오락실 목록과 별칭(2026-10-21: 서버·플레이어별이 아니다), 이름 확인, 게임별 방문 기록."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row

    async def create(self, *, name: str, created_by: str) -> Arcade:
        """새 오락실을 등록한다. 공백·대소문자를 무시하고 같은 이름이 이미 있으면 ValueError."""
        wanted = normalize_name(name)
        for existing in await self.list_all():
            if normalize_name(existing.name) == wanted:
                raise ValueError(f"'{existing.name}'은 이미 등록된 오락실이다")

        arcade_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        # arcades.guild_id 열은 예전 서버별 구조의 흔적이라 쓰지 않는다(빈 문자열).
        await self._conn.execute(
            "INSERT INTO arcades (id, guild_id, name, created_by, created_at) VALUES (?, '', ?, ?, ?)",
            (arcade_id, name, created_by, now),
        )
        await self._conn.commit()
        return Arcade(id=arcade_id, name=name, created_by=created_by, created_at=now)

    async def get(self, arcade_id: str) -> Arcade | None:
        cursor = await self._conn.execute("SELECT * FROM arcades WHERE id = ?", (arcade_id,))
        row = await cursor.fetchone()
        return _row_to_arcade(row) if row is not None else None

    async def list_all(self) -> list[Arcade]:
        cursor = await self._conn.execute("SELECT * FROM arcades ORDER BY name")
        return [_row_to_arcade(row) for row in await cursor.fetchall()]

    async def add_alias(self, arcade_id: str, *, player_id: str, alias: str) -> None:
        """별칭을 저장한다(전역). 이미 같은 오락실을 가리키면 아무것도 안 바꾼다(처음 등록한 사람을 지킨다).
        다른 오락실에 쓰인 별칭이거나 다른 오락실의 이름과 같으면 ValueError."""
        alias = alias.strip().lower()
        wanted = normalize_name(alias)
        for arcade in await self.list_all():
            if arcade.id != arcade_id and normalize_name(arcade.name) == wanted:
                raise ValueError(f"'{alias}'는 이미 '{arcade.name}'의 이름이다")
        cursor = await self._conn.execute("SELECT arcade_id FROM arcade_aliases WHERE alias = ?", (alias,))
        existing = await cursor.fetchone()
        if existing is not None:
            if existing["arcade_id"] != arcade_id:
                other = await self.get(existing["arcade_id"])
                raise ValueError(f"'{alias}'는 이미 '{other.name if other else '다른 오락실'}'의 별칭이다")
            return
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO arcade_aliases (alias, arcade_id, added_by, created_at) VALUES (?, ?, ?, ?)",
            (alias, arcade_id, player_id, now),
        )
        await self._conn.commit()

    async def record_visit(self, arcade_id: str, *, player_id: str, game: str) -> int:
        """새 방문을 남기고 그 행의 rowid를 돌려준다. `chunithm-arcade`가 그날 읽은
        설정을 이 방문에 매달려고 쓴다(arcade_visits에 따로 PK가 없어 rowid를 쓴다)."""
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            "INSERT INTO arcade_visits (arcade_id, player_id, game, visited_at) VALUES (?, ?, ?, ?)",
            (arcade_id, player_id, game, now),
        )
        await self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    async def visits_for(self, *, player_id: str, game: str) -> list[ArcadeVisit]:
        cursor = await self._conn.execute(
            "SELECT rowid AS id, * FROM arcade_visits WHERE player_id = ? AND game = ? ORDER BY visited_at",
            (player_id, game),
        )
        rows = await cursor.fetchall()
        return [
            ArcadeVisit(
                id=row["id"], arcade_id=row["arcade_id"], player_id=row["player_id"],
                game=row["game"], visited_at=row["visited_at"],
            )
            for row in rows
        ]

    async def resolve(self, raw: str, ctx: ToolContext) -> Arcade:
        """ArcadeRef 해석기. 이름이나 별칭이 (공백·대소문자 무시하고) 같으면 그 오락실이다. 아니면 비슷한
        이름·별칭을 후보로 돌려준다 — 모델이 "이거 맞아?"하고 되묻는 재료다. 새 오락실과 별칭은 여기서
        만들지 않는다(사용자가 맞다고 한 뒤 chunithm-arcade의 register_arcade가 저장한다)."""
        text = raw.strip()
        wanted = normalize_name(text)
        arcades = await self.list_all()
        for arcade in arcades:
            if normalize_name(arcade.name) == wanted:
                return arcade

        cursor = await self._conn.execute("SELECT alias, arcade_id FROM arcade_aliases")
        alias_rows = list(await cursor.fetchall())
        by_id = {arcade.id: arcade for arcade in arcades}
        for row in alias_rows:
            if normalize_name(row["alias"]) == wanted and row["arcade_id"] in by_id:
                return by_id[row["arcade_id"]]

        # 이름과 별칭을 한 후보 풀로 보고, 오락실 이름 기준으로 중복 없이 비슷한 순서로 돌려준다.
        owner: dict[str, str] = {arcade.name: arcade.name for arcade in arcades}
        for row in alias_rows:
            if row["arcade_id"] in by_id:
                owner[row["alias"]] = by_id[row["arcade_id"]].name
        candidates = list(dict.fromkeys(owner[n] for n in similar_names(text, owner, limit=6)))[:3]
        if candidates:
            raise ResolutionError(candidates=candidates)
        raise ResolutionError(message="그런 오락실은 등록돼 있지 않음")
