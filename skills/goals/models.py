# skills/goals/models.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import aiosqlite

_GOAL_COLUMNS = (
    "id, player_id, game, kind, target_ref, target_value, baseline_value, created_at, "
    "due_at, duration_tag, parent_goal_id, status, public, notify_offsets, guild_id"
)


@dataclass(slots=True, kw_only=True, frozen=True)
class GoalRow:
    id: str
    player_id: str
    game: str
    kind: str
    target_ref: dict[str, Any]
    target_value: float | None
    baseline_value: float | None
    created_at: str
    due_at: str | None
    duration_tag: str | None
    parent_goal_id: str | None
    status: str
    public: bool
    notify_offsets: list[int] | None
    guild_id: str | None


@dataclass(slots=True, kw_only=True, frozen=True)
class ScheduleRow:
    id: int
    goal_id: str
    schedule_id: int
    kind: str


@dataclass(slots=True, kw_only=True, frozen=True)
class ProgressRow:
    id: str
    goal_id: str
    sync_id: str | None
    recorded_at: str
    value: float | None
    achievement_ratio: float | None


def _goal_from_row(row: aiosqlite.Row) -> GoalRow:
    return GoalRow(
        id=row["id"],
        player_id=row["player_id"],
        game=row["game"],
        kind=row["kind"],
        target_ref=json.loads(row["target_ref"]),
        target_value=row["target_value"],
        baseline_value=row["baseline_value"],
        created_at=row["created_at"],
        due_at=row["due_at"],
        duration_tag=row["duration_tag"],
        parent_goal_id=row["parent_goal_id"],
        status=row["status"],
        public=bool(row["public"]),
        notify_offsets=json.loads(row["notify_offsets"]) if row["notify_offsets"] else None,
        guild_id=row["guild_id"],
    )


def _progress_from_row(row: aiosqlite.Row) -> ProgressRow:
    return ProgressRow(
        id=row["id"],
        goal_id=row["goal_id"],
        sync_id=row["sync_id"],
        recorded_at=row["recorded_at"],
        value=row["value"],
        achievement_ratio=row["achievement_ratio"],
    )


async def insert_goal(
    conn: aiosqlite.Connection,
    *,
    id: str,
    player_id: str,
    game: str = "chunithm",
    kind: str,
    target_ref: dict[str, Any],
    target_value: float | None,
    baseline_value: float | None,
    created_at: str,
    due_at: str | None,
    duration_tag: str | None,
    parent_goal_id: str | None,
    status: str = "in_progress",
    public: bool = False,
    notify_offsets: list[int] | None = None,
    guild_id: str | None = None,
) -> None:
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        f"INSERT INTO goals_goals ({_GOAL_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            id, player_id, game, kind, json.dumps(target_ref), target_value, baseline_value,
            created_at, due_at, duration_tag, parent_goal_id, status, int(public),
            json.dumps(notify_offsets) if notify_offsets is not None else None, guild_id,
        ),
    )
    await conn.commit()


async def fetch_goal(conn: aiosqlite.Connection, goal_id: str) -> GoalRow | None:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(f"SELECT {_GOAL_COLUMNS} FROM goals_goals WHERE id = ?", (goal_id,))
    row = await cursor.fetchone()
    return _goal_from_row(row) if row is not None else None


async def list_goals_for_player(
    conn: aiosqlite.Connection,
    player_id: str,
    *,
    kind: str | None = None,
    statuses: list[str] | None = None,
) -> list[GoalRow]:
    conn.row_factory = aiosqlite.Row
    query = f"SELECT {_GOAL_COLUMNS} FROM goals_goals WHERE player_id = ?"
    params: list[Any] = [player_id]
    if kind is not None:
        query += " AND kind = ?"
        params.append(kind)
    if statuses is not None:
        query += f" AND status IN ({','.join('?' for _ in statuses)})"
        params.extend(statuses)
    query += " ORDER BY created_at"
    cursor = await conn.execute(query, params)
    return [_goal_from_row(row) for row in await cursor.fetchall()]


async def find_rating_goal(conn: aiosqlite.Connection, player_id: str) -> GoalRow | None:
    rows = await list_goals_for_player(conn, player_id, kind="rating", statuses=["in_progress"])
    return rows[0] if rows else None


async def find_song_goal(
    conn: aiosqlite.Connection, player_id: str, *, song_title: str, difficulty: str
) -> GoalRow | None:
    for row in await list_goals_for_player(conn, player_id, kind="song", statuses=["in_progress"]):
        if row.target_ref.get("song_title") == song_title and row.target_ref.get("difficulty") == difficulty:
            return row
    return None


_UPDATABLE_FIELDS = frozenset(
    {"target_value", "due_at", "duration_tag", "parent_goal_id", "status", "public", "notify_offsets"}
)


async def update_goal(conn: aiosqlite.Connection, goal_id: str, **fields: Any) -> None:
    unknown = set(fields) - _UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"고칠 수 없는 필드다: {sorted(unknown)}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    values: list[Any] = []
    for name, value in fields.items():
        if name == "public":
            values.append(int(value))
        elif name == "notify_offsets":
            values.append(json.dumps(value) if value is not None else None)
        else:
            values.append(value)
    await conn.execute(f"UPDATE goals_goals SET {assignments} WHERE id = ?", (*values, goal_id))
    await conn.commit()


async def delete_goals(conn: aiosqlite.Connection, goal_ids: list[str]) -> int:
    """목표를 지우고, 같은 트랜잭션에서 그 진행 기록도 지우며, 하위 목표의 상위 연결을 끊는다
    (하위 목표 자체는 남는다)."""
    if not goal_ids:
        return 0
    placeholders = ",".join("?" for _ in goal_ids)
    await conn.execute(f"DELETE FROM goals_progress WHERE goal_id IN ({placeholders})", goal_ids)
    await conn.execute(
        f"UPDATE goals_goals SET parent_goal_id = NULL WHERE parent_goal_id IN ({placeholders})", goal_ids
    )
    cursor = await conn.execute(f"DELETE FROM goals_goals WHERE id IN ({placeholders})", goal_ids)
    await conn.commit()
    return cursor.rowcount


async def insert_progress(
    conn: aiosqlite.Connection,
    *,
    id: str,
    goal_id: str,
    sync_id: str | None,
    recorded_at: str,
    value: float | None,
    achievement_ratio: float | None,
) -> None:
    await conn.execute(
        "INSERT INTO goals_progress (id, goal_id, sync_id, recorded_at, value, achievement_ratio) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (id, goal_id, sync_id, recorded_at, value, achievement_ratio),
    )
    await conn.commit()


async def list_progress(conn: aiosqlite.Connection, goal_id: str) -> list[ProgressRow]:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        "SELECT id, goal_id, sync_id, recorded_at, value, achievement_ratio FROM goals_progress "
        "WHERE goal_id = ? ORDER BY recorded_at",
        (goal_id,),
    )
    return [_progress_from_row(row) for row in await cursor.fetchall()]


async def insert_schedule(conn: aiosqlite.Connection, *, goal_id: str, schedule_id: int, kind: str) -> None:
    await conn.execute(
        "INSERT INTO goals_notify_schedule (goal_id, schedule_id, kind) VALUES (?, ?, ?)",
        (goal_id, schedule_id, kind),
    )
    await conn.commit()


async def list_schedules(conn: aiosqlite.Connection, goal_id: str, *, kind: str | None = None) -> list[ScheduleRow]:
    conn.row_factory = aiosqlite.Row
    query = "SELECT id, goal_id, schedule_id, kind FROM goals_notify_schedule WHERE goal_id = ?"
    params: list[Any] = [goal_id]
    if kind is not None:
        query += " AND kind = ?"
        params.append(kind)
    cursor = await conn.execute(query, params)
    rows = await cursor.fetchall()
    return [
        ScheduleRow(id=row["id"], goal_id=row["goal_id"], schedule_id=row["schedule_id"], kind=row["kind"])
        for row in rows
    ]


async def delete_schedules(conn: aiosqlite.Connection, goal_id: str, *, kind: str | None = None) -> None:
    """목표의 예약 추적 행을 지운다(실제 schedule_once 취소는 호출하는 쪽이 먼저 한다).
    kind를 주면 그 종류만."""
    if kind is None:
        await conn.execute("DELETE FROM goals_notify_schedule WHERE goal_id = ?", (goal_id,))
    else:
        await conn.execute(
            "DELETE FROM goals_notify_schedule WHERE goal_id = ? AND kind = ?", (goal_id, kind)
        )
    await conn.commit()
