# core/scheduler.py
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import Any

import aiosqlite
import croniter


def next_fire_time(
    cron: str, after: datetime.datetime, *, quiet_start: datetime.time, quiet_end: datetime.time
) -> datetime.datetime:
    """cron의 다음 실행 시각을 구하고, 새벽 정지 시간대(quiet_start <= t < quiet_end) 안에
    걸리면 그 시간대가 끝나는 시각으로 민다. 정지 시간대가 자정을 걸치지 않는 경우만
    다룬다(기본값 02~06시가 그렇다) — 자정을 걸치는 설정은 미확인 사항으로 남긴다."""
    candidate = croniter.croniter(cron, after).get_next(datetime.datetime)
    candidate_time = candidate.time()
    if quiet_start <= candidate_time < quiet_end:
        candidate = candidate.replace(
            hour=quiet_end.hour, minute=quiet_end.minute, second=0, microsecond=0
        )
    return candidate


@dataclass(slots=True, kw_only=True, frozen=True)
class OnceSchedule:
    owner_skill: str
    event_name: str
    payload: dict[str, Any]


class Scheduler:
    """@schedule 데코레이터가 붙은 함수 자체는 매 기동마다 레지스트리가 다시 찾아낸다.
    여기 DB에는 "다음/마지막 실행 시각" 같은 상태만 (owner_skill, fn_name) 키로 둔다."""

    def __init__(self, conn: aiosqlite.Connection, *, quiet_start: datetime.time, quiet_end: datetime.time) -> None:
        self._conn = conn
        self._quiet_start = quiet_start
        self._quiet_end = quiet_end

    async def sync_recurring(
        self, specs: list[tuple[str, str, str, bool]], *, now: datetime.datetime
    ) -> None:
        for owner_skill, fn_name, cron, catch_up in specs:
            cursor = await self._conn.execute(
                "SELECT cron FROM schedule_recurring WHERE owner_skill = ? AND fn_name = ?",
                (owner_skill, fn_name),
            )
            row = await cursor.fetchone()
            if row is not None and row[0] == cron:
                continue
            next_run = next_fire_time(cron, now, quiet_start=self._quiet_start, quiet_end=self._quiet_end)
            await self._conn.execute(
                "INSERT INTO schedule_recurring (owner_skill, fn_name, cron, catch_up, next_run_at, last_run_at) "
                "VALUES (?, ?, ?, ?, ?, NULL) "
                "ON CONFLICT (owner_skill, fn_name) DO UPDATE SET "
                "cron = excluded.cron, catch_up = excluded.catch_up, next_run_at = excluded.next_run_at",
                (owner_skill, fn_name, cron, int(catch_up), next_run.isoformat()),
            )
        await self._conn.commit()

    async def startup_catch_up(self, *, now: datetime.datetime) -> list[tuple[str, str]]:
        """기동 시 한 번 부른다. catch_up이 켜졌고 그 밀린 몫이 '오늘' 날짜인 것만 돌려준다.
        그 밖의 밀린 몫(catch_up 꺼짐, 또는 여러 날 전)은 돌리지 않고 다음 시각으로 민다."""
        cursor = await self._conn.execute(
            "SELECT owner_skill, fn_name, cron, catch_up, next_run_at FROM schedule_recurring "
            "WHERE next_run_at < ?",
            (now.isoformat(),),
        )
        rows = await cursor.fetchall()
        today = now.date().isoformat()
        to_run: list[tuple[str, str]] = []
        for owner_skill, fn_name, cron, catch_up, next_run_at in rows:
            if catch_up and next_run_at[:10] == today:
                to_run.append((owner_skill, fn_name))
            else:
                rescheduled = next_fire_time(cron, now, quiet_start=self._quiet_start, quiet_end=self._quiet_end)
                await self._conn.execute(
                    "UPDATE schedule_recurring SET next_run_at = ? WHERE owner_skill = ? AND fn_name = ?",
                    (rescheduled.isoformat(), owner_skill, fn_name),
                )
        await self._conn.commit()
        return to_run

    async def mark_run(self, owner_skill: str, fn_name: str, *, cron: str, ran_at: datetime.datetime) -> None:
        next_run = next_fire_time(cron, ran_at, quiet_start=self._quiet_start, quiet_end=self._quiet_end)
        await self._conn.execute(
            "UPDATE schedule_recurring SET last_run_at = ?, next_run_at = ? "
            "WHERE owner_skill = ? AND fn_name = ?",
            (ran_at.isoformat(), next_run.isoformat(), owner_skill, fn_name),
        )
        await self._conn.commit()

    async def schedule_once(
        self, *, owner_skill: str, event_name: str, payload: dict[str, Any],
        fire_at: datetime.datetime, catch_up: bool,
    ) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO schedule_once (owner_skill, event_name, payload, fire_at, catch_up, fired_at) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (owner_skill, event_name, json.dumps(payload), fire_at.isoformat(), int(catch_up)),
        )
        await self._conn.commit()
        row_id = cursor.lastrowid
        assert row_id is not None
        return row_id

    async def startup_catch_up_once(self, *, now: datetime.datetime) -> list[OnceSchedule]:
        """한 번 도는 예약 중 아직 안 돈 것을 본다. '오늘' 날짜로 예정됐던 것만 낸다 —
        catch_up 여부와 무관하게 하루가 지나면 더는 의미가 없다고 보고 그냥 닫는다."""
        cursor = await self._conn.execute(
            "SELECT id, owner_skill, event_name, payload, fire_at FROM schedule_once "
            "WHERE fired_at IS NULL AND fire_at <= ?",
            (now.isoformat(),),
        )
        rows = await cursor.fetchall()
        today = now.date().isoformat()
        due: list[OnceSchedule] = []
        for row_id, owner_skill, event_name, payload, fire_at in rows:
            if fire_at[:10] == today:
                due.append(
                    OnceSchedule(owner_skill=owner_skill, event_name=event_name, payload=json.loads(payload))
                )
            await self._conn.execute(
                "UPDATE schedule_once SET fired_at = ? WHERE id = ?", (now.isoformat(), row_id)
            )
        await self._conn.commit()
        return due
