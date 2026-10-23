# skills/updates/models.py
from __future__ import annotations

import json
from dataclasses import dataclass

import aiosqlite

_NOTICE_COLUMNS = (
    "id, game, region, url, title, posted_at, update_date, version_name, schedule_id, notified, created_at"
)
_SNAPSHOT_COLUMNS = "id, game, region, checked_at, song_ids_json, added_json, removed_json"


@dataclass(slots=True, kw_only=True, frozen=True)
class NoticeRow:
    id: int
    region: str
    url: str
    title: str
    posted_at: str
    update_date: str | None
    version_name: str | None
    schedule_id: int | None
    notified: bool


@dataclass(slots=True, kw_only=True, frozen=True)
class SnapshotRow:
    id: int
    region: str
    checked_at: str
    song_ids: dict[str, str]
    added: list[tuple[str, str]]
    removed: list[tuple[str, str]]


def _notice_from_row(row: aiosqlite.Row) -> NoticeRow:
    return NoticeRow(
        id=row["id"], region=row["region"], url=row["url"], title=row["title"],
        posted_at=row["posted_at"], update_date=row["update_date"], version_name=row["version_name"],
        schedule_id=row["schedule_id"], notified=bool(row["notified"]),
    )


def _snapshot_from_row(row: aiosqlite.Row) -> SnapshotRow:
    return SnapshotRow(
        id=row["id"], region=row["region"], checked_at=row["checked_at"],
        song_ids=json.loads(row["song_ids_json"]),
        added=[tuple(pair) for pair in json.loads(row["added_json"])],
        removed=[tuple(pair) for pair in json.loads(row["removed_json"])],
    )


async def insert_notice(
    conn: aiosqlite.Connection,
    *,
    region: str,
    url: str,
    title: str,
    posted_at: str,
    update_date: str | None,
    version_name: str | None,
    created_at: str,
    game: str = "chunithm",
) -> int:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        "INSERT INTO updates_notices (game, region, url, title, posted_at, update_date, version_name, "
        "schedule_id, notified, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, ?)",
        (game, region, url, title, posted_at, update_date, version_name, created_at),
    )
    await conn.commit()
    row_id = cursor.lastrowid
    assert row_id is not None
    return row_id


async def notice_exists(conn: aiosqlite.Connection, *, region: str, url: str) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM updates_notices WHERE region = ? AND url = ?", (region, url)
    )
    return await cursor.fetchone() is not None


async def set_schedule_id(conn: aiosqlite.Connection, notice_id: int, schedule_id: int) -> None:
    await conn.execute("UPDATE updates_notices SET schedule_id = ? WHERE id = ?", (schedule_id, notice_id))
    await conn.commit()


async def mark_notified(conn: aiosqlite.Connection, notice_id: int) -> None:
    await conn.execute("UPDATE updates_notices SET notified = 1 WHERE id = ?", (notice_id,))
    await conn.commit()


async def get_notice(conn: aiosqlite.Connection, notice_id: int) -> NoticeRow | None:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(f"SELECT {_NOTICE_COLUMNS} FROM updates_notices WHERE id = ?", (notice_id,))
    row = await cursor.fetchone()
    return _notice_from_row(row) if row is not None else None


async def latest_version_name(conn: aiosqlite.Connection, *, region: str) -> str | None:
    """그 지역에서 지금까지 알아낸 가장 최근 버전명(없으면 None — 처음 켤 때는 기준이 없다)."""
    cursor = await conn.execute(
        "SELECT version_name FROM updates_notices WHERE region = ? AND version_name IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (region,),
    )
    row = await cursor.fetchone()
    return row[0] if row is not None else None


async def next_update_notice(conn: aiosqlite.Connection, *, region: str, today: str) -> NoticeRow | None:
    """오늘(today, ISO 날짜) 이후로 예정된 가장 가까운 업데이트. 없으면 None."""
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        f"SELECT {_NOTICE_COLUMNS} FROM updates_notices WHERE region = ? AND update_date >= ? "
        "ORDER BY update_date ASC LIMIT 1",
        (region, today),
    )
    row = await cursor.fetchone()
    return _notice_from_row(row) if row is not None else None


async def insert_song_snapshot(
    conn: aiosqlite.Connection,
    *,
    region: str,
    checked_at: str,
    song_ids: dict[str, str],
    added: list[tuple[str, str]],
    removed: list[tuple[str, str]],
    game: str = "chunithm",
) -> None:
    await conn.execute(
        "INSERT INTO updates_song_snapshots (game, region, checked_at, song_ids_json, added_json, removed_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (game, region, checked_at, json.dumps(song_ids), json.dumps(added), json.dumps(removed)),
    )
    await conn.commit()


async def latest_song_snapshot(conn: aiosqlite.Connection, *, region: str) -> SnapshotRow | None:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM updates_song_snapshots WHERE region = ? "
        "ORDER BY checked_at DESC LIMIT 1",
        (region,),
    )
    row = await cursor.fetchone()
    return _snapshot_from_row(row) if row is not None else None


async def song_snapshot_at_or_before(conn: aiosqlite.Connection, *, region: str, on_date: str) -> SnapshotRow | None:
    """그 날짜(on_date, ISO 날짜) 당일이나 그 전의 가장 최근 저장본. `/신곡`에 날짜를 줬을 때 쓴다."""
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM updates_song_snapshots WHERE region = ? AND date(checked_at) <= date(?) "
        "ORDER BY checked_at DESC LIMIT 1",
        (region, on_date),
    )
    row = await cursor.fetchone()
    return _snapshot_from_row(row) if row is not None else None


async def song_snapshots_since(conn: aiosqlite.Connection, *, region: str, since: str) -> list[SnapshotRow]:
    """since(ISO 시각) 뒤에 생긴 저장본 전부, 오래된 순. 동기화 리포트 조각이 "지난 동기화 뒤"
    추가된 곡을 모으는 데 쓴다."""
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        f"SELECT {_SNAPSHOT_COLUMNS} FROM updates_song_snapshots WHERE region = ? AND checked_at > ? "
        "ORDER BY checked_at ASC",
        (region, since),
    )
    return [_snapshot_from_row(row) for row in await cursor.fetchall()]
