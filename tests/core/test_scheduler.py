# ruff: noqa: DTZ001 - 이 테스트가 다루는 cron/정지 시간대는 서버 로컬 벽시계 시간이다(UTC 타임스탬프가 아니다).
# 모든 datetime.datetime(...) 생성자는 그 로컬 시각을 그대로 나타내려고 의도적으로 naive로 쓴다.
import datetime
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core.scheduler import OnceSchedule, Scheduler, next_fire_time

_MIGRATION = Path("migrations/core/006_schedules.sql").read_text()

QUIET_START = datetime.time(2, 0)
QUIET_END = datetime.time(6, 0)


def test_next_fire_time_outside_quiet_hours_unaffected() -> None:
    after = datetime.datetime(2026, 10, 10, 1, 0)
    result = next_fire_time("0 9 * * *", after, quiet_start=QUIET_START, quiet_end=QUIET_END)
    assert result == datetime.datetime(2026, 10, 10, 9, 0)


def test_next_fire_time_inside_quiet_hours_shifts_to_quiet_end() -> None:
    after = datetime.datetime(2026, 10, 10, 1, 0)
    result = next_fire_time("0 3 * * *", after, quiet_start=QUIET_START, quiet_end=QUIET_END)
    assert result == datetime.datetime(2026, 10, 10, 6, 0)


def test_next_fire_time_exactly_at_quiet_end_is_unaffected() -> None:
    after = datetime.datetime(2026, 10, 10, 1, 0)
    result = next_fire_time("0 6 * * *", after, quiet_start=QUIET_START, quiet_end=QUIET_END)
    assert result == datetime.datetime(2026, 10, 10, 6, 0)


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_sync_recurring_creates_row_with_next_run(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    now = datetime.datetime(2026, 10, 10, 1, 0)

    await scheduler.sync_recurring([("chunithm-updates", "check_updates", "0 9 * * *", True)], now=now)

    due = await scheduler.startup_catch_up(now=datetime.datetime(2026, 10, 10, 9, 0))
    assert due == []  # 아직 '오늘' 몫이 지나지 않았다(9시 정각은 <= 9시라 아래에서 확인)


async def test_sync_recurring_is_idempotent_across_restarts(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    now = datetime.datetime(2026, 10, 10, 1, 0)

    await scheduler.sync_recurring([("chunithm-updates", "check_updates", "0 9 * * *", True)], now=now)
    # "재시작"을 흉내 낸다 — 같은 스킬 코드를 다시 동기화해도 next_run_at이 안 바뀐다.
    await scheduler.sync_recurring([("chunithm-updates", "check_updates", "0 9 * * *", True)], now=now)

    cursor = await conn.execute(
        "SELECT next_run_at FROM schedule_recurring WHERE owner_skill = ? AND fn_name = ?",
        ("chunithm-updates", "check_updates"),
    )
    rows = list(await cursor.fetchall())
    assert len(rows) == 1


async def test_startup_catch_up_runs_only_todays_missed_schedule(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    boot_day1 = datetime.datetime(2026, 10, 9, 1, 0)
    await scheduler.sync_recurring([("chunithm-updates", "check_updates", "0 9 * * *", True)], now=boot_day1)

    # 1일차: 9시가 지나도록 봇이 안 꺼져서 한 번도 안 돌았다고 가정 — 2일차 아침에 깨운다.
    boot_day2 = datetime.datetime(2026, 10, 10, 1, 0)
    due_day2 = await scheduler.startup_catch_up(now=boot_day2)
    assert due_day2 == []  # 1일차 9시는 이미 지난 날이라 캐치업 대상이 아니다(그날 것만)

    # 2일차 9시가 지난 뒤(정지 시간대 안이라 밀렸다고 가정) 다시 깨운다.
    later_same_day = datetime.datetime(2026, 10, 10, 9, 30)
    due_same_day = await scheduler.startup_catch_up(now=later_same_day)
    assert due_same_day == [("chunithm-updates", "check_updates")]

    await scheduler.mark_run(
        "chunithm-updates", "check_updates", cron="0 9 * * *", ran_at=later_same_day
    )
    # 한 번 더 부르면 이미 처리해 더는 밀린 게 없다.
    assert await scheduler.startup_catch_up(now=later_same_day) == []


async def test_startup_catch_up_ignores_non_catch_up_schedule(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    now = datetime.datetime(2026, 10, 10, 1, 0)
    await scheduler.sync_recurring([("web-qa", "refresh_index", "0 3 * * *", False)], now=now)

    due = await scheduler.startup_catch_up(now=datetime.datetime(2026, 10, 10, 9, 0))
    assert due == []  # catch_up=False라 밀려도 캐치업하지 않는다 — 다음 날로 넘어간다


async def test_schedule_once_catches_up_only_same_day(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    fire_at = datetime.datetime(2026, 10, 10, 20, 0)
    await scheduler.schedule_once(
        owner_skill="chunithm-goals", event_name="chunithm.goal.due",
        payload={"goal_id": "g1"}, fire_at=fire_at, catch_up=True,
    )

    same_day_wakeup = datetime.datetime(2026, 10, 10, 23, 0)
    due = await scheduler.startup_catch_up_once(now=same_day_wakeup)
    assert due == [OnceSchedule(owner_skill="chunithm-goals", event_name="chunithm.goal.due", payload={"goal_id": "g1"})]


async def test_schedule_once_gives_up_after_the_day_passes(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    fire_at = datetime.datetime(2026, 10, 10, 20, 0)
    await scheduler.schedule_once(
        owner_skill="chunithm-goals", event_name="chunithm.goal.due",
        payload={"goal_id": "g1"}, fire_at=fire_at, catch_up=True,
    )

    next_day_wakeup = datetime.datetime(2026, 10, 11, 7, 0)
    due = await scheduler.startup_catch_up_once(now=next_day_wakeup)
    assert due == []


async def test_schedule_once_ignores_non_catch_up_even_same_day(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    fire_at = datetime.datetime(2026, 10, 10, 20, 0)
    await scheduler.schedule_once(
        owner_skill="chunithm-goals", event_name="chunithm.goal.due",
        payload={"goal_id": "g1"}, fire_at=fire_at, catch_up=False,
    )

    same_day_wakeup = datetime.datetime(2026, 10, 10, 23, 0)
    due = await scheduler.startup_catch_up_once(now=same_day_wakeup)
    assert due == []  # catch_up=False라 같은 날이어도 돌지 않는다 — 그냥 닫힌다
