# core/scheduler.py
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import Any

import aiosqlite
import croniter


def _shift_past_quiet_hours(
    candidate: datetime.datetime, *, quiet_start: datetime.time, quiet_end: datetime.time
) -> datetime.datetime:
    """새벽 정지 시간대(quiet_start <= t < quiet_end) 안에 걸리면 그 시간대가 끝나는 시각으로
    민다. 정지 시간대가 자정을 걸치지 않는 경우만 다룬다(기본값 02~06시가 그렇다) — 자정을
    걸치는 설정은 미확인 사항으로 남긴다."""
    candidate_time = candidate.time()
    if quiet_start <= candidate_time < quiet_end:
        return candidate.replace(hour=quiet_end.hour, minute=quiet_end.minute, second=0, microsecond=0)
    return candidate


def next_fire_time(
    cron: str, after: datetime.datetime, *, quiet_start: datetime.time, quiet_end: datetime.time
) -> datetime.datetime:
    """cron의 다음 실행 시각을 구하고, 새벽 정지 시간대 밖으로 민다(_shift_past_quiet_hours)."""
    candidate = croniter.croniter(cron, after).get_next(datetime.datetime)
    return _shift_past_quiet_hours(candidate, quiet_start=quiet_start, quiet_end=quiet_end)


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
        """fire_at이 새벽 정지 시간대 안에 걸리면 그 시간대가 끝나는 시각으로 민다(recurring과
        같은 규칙). 돌려주는 행 id로 나중에 cancel_once를 부를 수 있다(기한 전 달성 등)."""
        fire_at = _shift_past_quiet_hours(fire_at, quiet_start=self._quiet_start, quiet_end=self._quiet_end)
        cursor = await self._conn.execute(
            "INSERT INTO schedule_once (owner_skill, event_name, payload, fire_at, catch_up, fired_at) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (owner_skill, event_name, json.dumps(payload), fire_at.isoformat(), int(catch_up)),
        )
        await self._conn.commit()
        row_id = cursor.lastrowid
        assert row_id is not None
        return row_id

    async def cancel_once(self, schedule_id: int) -> None:
        """아직 안 돈 한 번짜리 예약을 지운다(예: 목표가 기한 전에 달성돼 남은 알림이 필요
        없어졌을 때). 이미 돈 것(fired_at이 찬 행)은 그대로 둔다 — 지울 대상이 아니다."""
        await self._conn.execute(
            "DELETE FROM schedule_once WHERE id = ? AND fired_at IS NULL", (schedule_id,)
        )
        await self._conn.commit()

    async def startup_catch_up_once(self, *, now: datetime.datetime) -> list[OnceSchedule]:
        """한 번 도는 예약 중 아직 안 돈 것을 본다. catch_up이 켜졌고 '오늘' 날짜로
        예정됐던 것만 낸다 — recurring 쪽(startup_catch_up)과 완전히 같은 규칙이다.
        catch_up이 꺼졌거나 오늘이 아니면 그냥 닫는다(다시는 안 돈다 — one-off라 미룰
        '다음 시각'이 없다). '밀렸다'의 기준은 startup_catch_up과 같이 엄격히 과거다."""
        cursor = await self._conn.execute(
            "SELECT id, owner_skill, event_name, payload, fire_at, catch_up FROM schedule_once "
            "WHERE fired_at IS NULL AND fire_at < ?",
            (now.isoformat(),),
        )
        rows = await cursor.fetchall()
        today = now.date().isoformat()
        due: list[OnceSchedule] = []
        for row_id, owner_skill, event_name, payload, fire_at, catch_up in rows:
            if catch_up and fire_at[:10] == today:
                due.append(
                    OnceSchedule(owner_skill=owner_skill, event_name=event_name, payload=json.loads(payload))
                )
            await self._conn.execute(
                "UPDATE schedule_once SET fired_at = ? WHERE id = ?", (now.isoformat(), row_id)
            )
        await self._conn.commit()
        return due

    async def poll_due_recurring(self, *, now: datetime.datetime) -> list[tuple[str, str]]:
        """운영 중 폴링 루프가 부른다(core/bot.py). startup_catch_up과 달리 '오늘' 제한이나
        catch_up 플래그를 안 본다 — 지금 돌고 있는 중이라 밀린 게 아니라 제때 맞춰 도는
        경우이기 때문이다. mark_run은 호출한 쪽이 실행 뒤에 직접 부른다."""
        cursor = await self._conn.execute(
            "SELECT owner_skill, fn_name FROM schedule_recurring WHERE next_run_at <= ?",
            (now.isoformat(),),
        )
        rows = await cursor.fetchall()
        return [(owner_skill, fn_name) for owner_skill, fn_name in rows]

    async def poll_due_once(self, *, now: datetime.datetime) -> list[OnceSchedule]:
        """운영 중 폴링 루프가 부른다. startup_catch_up_once와 달리 '오늘' 제한이나 catch_up
        플래그 없이, 기한이 된 것을 전부 낸다(제때 도는 경우라 캐치업 규칙이 필요 없다)."""
        cursor = await self._conn.execute(
            "SELECT id, owner_skill, event_name, payload FROM schedule_once "
            "WHERE fired_at IS NULL AND fire_at <= ?",
            (now.isoformat(),),
        )
        rows = await cursor.fetchall()
        due: list[OnceSchedule] = []
        for row_id, owner_skill, event_name, payload in rows:
            due.append(OnceSchedule(owner_skill=owner_skill, event_name=event_name, payload=json.loads(payload)))
            await self._conn.execute(
                "UPDATE schedule_once SET fired_at = ? WHERE id = ?", (now.isoformat(), row_id)
            )
        await self._conn.commit()
        return due
