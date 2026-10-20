# skills/yuni-bond/tests/test_yuni_bond.py
from __future__ import annotations

import importlib.util
import sys
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus, Section
from core.tables import RowRef, render_text
from core.tool_types import Display, ToolContext
from services.chunithm.records import ChartUpdate, RatingDeltas, SessionInfo, SyncDiff
from services.chunithm.songdb import SongDbService
from services.common.arcades import Arcade

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_SQL = Path(__file__).parent.parent / "migrations" / "001_init.sql"
_TODAY = date(2026, 10, 19)


def _load_skill() -> ModuleType:
    # 형제 모듈 이름이 겹치지 않게 이 시험 전용 이름으로 불러온다.
    spec = importlib.util.spec_from_file_location("_yuni_bond_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load_skill()
    monkeypatch.setattr(module, "_today", lambda: _TODAY)
    return module


@pytest.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_SQL.read_text())
    try:
        yield conn
    finally:
        await conn.close()


@dataclass
class _FakeSettings:
    values: dict[str, Any] = field(default_factory=dict)

    async def get_value(self, *, scope: str, scope_id: str | None, skill: str, name: str, default: Any) -> Any:
        return self.values.get(name, default)


@dataclass
class _FakePlayer:
    first_seen_at: str = "2026-01-02T03:04:05+00:00"


class _FakePlayers:
    async def get_player(self, player_id: str) -> _FakePlayer | None:
        return _FakePlayer()


class _FakeRecords:
    def __init__(self, *, diff: SyncDiff | None = None, deltas: RatingDeltas | None = None) -> None:
        self.diff = diff
        self.deltas = deltas

    async def sync_diff(self, sync_id: str) -> SyncDiff | None:
        return self.diff

    async def rating_deltas(self, player_id: str) -> RatingDeltas | None:
        return self.deltas


class _GatherBus(EventBus):
    def __init__(self, sections: list[Section] | None = None) -> None:
        super().__init__()
        self.sections = sections or []
        self.calls: list[tuple[str, Any]] = []

    async def emit_gather(self, event_name: str, payload: Any, *, player_id: str, guild_id: str | None) -> list[Section]:
        self.calls.append((event_name, payload))
        return list(self.sections)


def _ctx(
    db: aiosqlite.Connection, *, player_id: str = "p1", records: _FakeRecords | None = None,
    settings: _FakeSettings | None = None, songdb: SongDbService | None = None, bus: EventBus | None = None,
    guild_id: str | None = "g1",
) -> ToolContext:
    services: dict[str, object] = {
        "players": _FakePlayers(), "records": records or _FakeRecords(), "settings": settings or _FakeSettings(),
        "songdb": songdb or SongDbService(),
    }
    return ToolContext(
        player_id=player_id, tags=frozenset(), services=services, db=db, event_bus=bus or EventBus(), guild_id=guild_id,
    )


def _update(
    title: str = "B.B.K.K.B.K.K.", difficulty: str = "MAS", *, old_rank: str | None, new_rank: str,
    old_score: int | None = 1_000_000, new_score: int = 1_007_500, session_id: str = "s1",
) -> ChartUpdate:
    return ChartUpdate(
        song_title=title, difficulty=difficulty, old_score=old_score, new_score=new_score, old_rank=old_rank,
        new_rank=new_rank, old_rating=None, new_rating=None, session_id=session_id, estimated_session=False,
        entered_frame=False,
    )


def _diff(updates: list[ChartUpdate], *, play_date: str = "2026-10-18") -> SyncDiff:
    return SyncDiff(
        sync_id="sync1", sessions=[SessionInfo(id="s1", play_date=play_date, estimated=False)], chart_updates=updates,
        unchanged_plays=[], frame_exits=[], previous_computed_rating=None, previous_best30_average=None,
        previous_new20_average=None,
    )


def _deltas(previous: str | None, current: str | None) -> RatingDeltas:
    return RatingDeltas(
        computed_rating=Decimal(current) if current else None, best30_average=None, new20_average=None,
        has_previous=previous is not None, previous_computed_rating=Decimal(previous) if previous else None,
        previous_best30_average=None, previous_new20_average=None,
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "player_id": "p1", "sync_id": "sync1", "has_new_plays": True, "is_first_sync": False,
        "previous_synced_at": "2026-10-18T10:00:00+00:00",
    }
    return {**base, **overrides}


async def _rows(db: aiosqlite.Connection, sql: str, *params: Any) -> list[aiosqlite.Row]:
    return list(await (await db.execute(sql, params)).fetchall())


# ---------------------------------------------------------------- 사건 기록


async def test_first_seen_is_recorded_once(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await skill.on_first_seen(_ctx(db), {"player_id": "p1"})
    await skill.on_first_seen(_ctx(db), {"player_id": "p1"})

    rows = await _rows(db, "SELECT kind, occurred_on, game FROM yuni_bond_milestones")
    assert [(r["kind"], r["occurred_on"], r["game"]) for r in rows] == [("first_met", "2026-10-19", "common")]


async def test_history_falls_back_to_the_players_first_seen_date(skill: ModuleType, db: aiosqlite.Connection) -> None:
    result = await skill.history(_ctx(db), kind="first_met")

    [event] = result.data["events"]
    assert event["date"] == "2026-01-02"
    assert event["days_ago"] == 290
    assert event["date_label"] == "1월 2일"


async def test_goal_completed_keeps_the_registered_date(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await skill.on_goal_completed(
        _ctx(db),
        {"player_id": "p1", "goal_id": "g9", "label": "크로노 MAS SSS", "created_at": "2026-10-01T00:00:00+00:00",
         "completed_at": "2026-10-11T00:00:00+00:00"},
    )

    result = await skill.history(_ctx(db), kind="goal_done")

    [event] = result.data["events"]
    assert (event["goal"], event["days_taken"], event["date_label"]) == ("크로노 MAS SSS", 10, "10월 11일")
    table = result.table
    assert table is not None and table.subject == "p1" and table.unit == "기록"
    [row] = table.rows
    assert row.ref.kind == "yuni-bond.event" and row.ref.key == '["goal_done", "2026-10-11", "", ""]'
    assert (row.cell("날짜").raw, row.cell("날짜").text) == ("2026-10-11", "10월 11일")
    assert (row.cell("종류").raw, row.cell("종류").text) == ("goal_done", "To-Do 달성")
    assert not row.cell("곡").filled  # 곡과 무관한 기록은 곡 칸이 빈다
    assert row.cell("내용").text == "크로노 MAS SSS · 10일 걸림"


# ---------------------------------------------------------------- 동기화


async def test_first_sss_is_recorded_with_the_time_it_took(skill: ModuleType, db: aiosqlite.Connection) -> None:
    first = _FakeRecords(diff=_diff([_update(old_rank="SS+", new_rank="SS+", new_score=1_005_000)], play_date="2026-10-01"))
    await skill.on_sync_completed(_ctx(db, records=first), _payload())

    later = _FakeRecords(diff=_diff([_update(old_rank="SS+", new_rank="SSS")], play_date="2026-10-18"))
    section = await skill.on_sync_completed(_ctx(db, records=later), _payload())

    assert section is not None
    assert section.display is None
    assert section.data["first_achievements"] == [
        {"song_title": "B.B.K.K.B.K.K.", "difficulty": "MAS", "rank": "SSS", "days_on_chart": 17}
    ]
    assert "SSS" in (section.hint or "")


async def test_a_rank_already_held_is_not_a_first(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([_update(old_rank="SSS", new_rank="SSS", new_score=1_008_000)]))

    section = await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert section is None
    assert await _rows(db, "SELECT 1 FROM yuni_bond_milestones WHERE kind = 'rank_first'") == []


async def test_reaching_sss_plus_in_one_go_records_both_tiers(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([_update(old_rank="SS+", new_rank="SSS+", new_score=1_009_000)]))
    await skill.on_sync_completed(_ctx(db, records=records), _payload())

    rows = await _rows(db, "SELECT value FROM yuni_bond_milestones WHERE kind = 'rank_first' ORDER BY value")
    assert [r["value"] for r in rows] == ["SSS", "SSS+"]


async def test_the_same_sync_twice_records_nothing_new(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([_update(old_rank="SS+", new_rank="SSS")]))
    await skill.on_sync_completed(_ctx(db, records=records), _payload())
    again = await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert again is None


async def test_rating_milestone_gives_the_remaining_amount_computed_in_code(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("16.20", "16.26"))

    section = await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert section is not None
    assert section.data["rating_milestone"] == "16.25"
    assert section.data["next_milestone"] == "16.50"
    assert section.data["remaining_to_next"] == "0.24"
    assert "16.25" in (section.hint or "")


async def test_a_milestone_is_not_repeated_after_the_rating_dips_and_returns(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    up = _FakeRecords(diff=_diff([]), deltas=_deltas("16.20", "16.26"))
    await skill.on_sync_completed(_ctx(db, records=up), _payload())
    again = _FakeRecords(diff=_diff([]), deltas=_deltas("16.20", "16.27"))

    assert await skill.on_sync_completed(_ctx(db, records=again), _payload()) is None


async def test_a_higher_milestone_reports_the_days_since_the_previous_one(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    await db.execute(
        "INSERT INTO yuni_bond_milestones (player_id, kind, value, occurred_on) VALUES "
        "('p1', 'rating_milestone', '16.25', '2026-10-09')"
    )
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("16.45", "16.52"))

    section = await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert section is not None
    assert section.data["rating_milestone"] == "16.50"
    assert section.data["days_since_previous_milestone"] == 10


async def test_no_hint_when_nothing_changed(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("16.20", "16.26"))

    assert await skill.on_sync_completed(_ctx(db, records=records), _payload(has_new_plays=False)) is None
    assert await _rows(db, "SELECT 1 FROM yuni_bond_milestones") == []


async def test_no_hint_when_the_sync_has_nothing_to_say(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([_update(old_rank="S", new_rank="SS", new_score=990_000)]), deltas=_deltas("16.21", "16.22"))

    assert await skill.on_sync_completed(_ctx(db, records=records), _payload()) is None


async def test_the_hint_setting_off_still_records_but_gives_no_hint(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("16.20", "16.26"))
    settings = _FakeSettings({"동기화 때 덧붙이는 말": False})

    section = await skill.on_sync_completed(_ctx(db, records=records, settings=settings), _payload())

    assert section is None
    assert len(await _rows(db, "SELECT 1 FROM yuni_bond_milestones WHERE kind = 'rating_milestone'")) == 1


async def test_welcome_back_after_the_configured_number_of_days(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("16.20", "16.21"))

    away = await skill.on_sync_completed(
        _ctx(db, records=records), _payload(previous_synced_at="2026-10-01T00:00:00+00:00")
    )
    recent = await skill.on_sync_completed(
        _ctx(db, records=records), _payload(previous_synced_at="2026-10-10T00:00:00+00:00")
    )
    custom = await skill.on_sync_completed(
        _ctx(db, records=records, settings=_FakeSettings({"오랜만의 동기화 기준": 5})),
        _payload(previous_synced_at="2026-10-10T00:00:00+00:00"),
    )

    assert away is not None and away.data["days_since_last_sync"] == 18
    assert recent is None
    assert custom is not None and custom.data["days_since_last_sync"] == 9


# ---------------------------------------------------------------- 오래 붙잡은 곡 / 곡 조회


async def _sync_scores(skill: ModuleType, db: aiosqlite.Connection, scores: list[int]) -> None:
    for index, score in enumerate(scores):
        records = _FakeRecords(
            diff=_diff([_update(old_rank="S", new_rank="SS", new_score=score)], play_date=f"2026-10-{index + 1:02d}")
        )
        await skill.on_sync_completed(_ctx(db, records=records), _payload())


async def test_a_chart_updated_many_times_shows_up_as_a_long_haul(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await _sync_scores(skill, db, [990_000, 992_000, 994_000, 996_000, 998_000])

    result = await skill.history(_ctx(db), kind="long_haul")

    [event] = result.data["events"]
    assert (event["updates"], event["first_score"], event["latest_score"]) == (5, 990_000, 998_000)
    assert event["since_label"] == "10월 1일"
    [row] = result.table.rows
    assert row.cell("곡").raw == "B.B.K.K.B.K.K." and row.cell("난이도").text == "MAS"
    assert row.cell("내용").text == "5번 갱신, 990,000점 → 998,000점 · 10월 1일부터"


async def test_four_updates_are_not_yet_a_long_haul(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await _sync_scores(skill, db, [990_000, 992_000, 994_000, 996_000])

    assert (await skill.history(_ctx(db), kind="long_haul")).data["events"] == []


async def test_song_viewed_returns_only_events_of_that_song(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(
        diff=_diff([_update(old_rank="SS+", new_rank="SSS"), _update("Other", "EXP", old_rank="S", new_rank="SSS")])
    )
    await skill.on_sync_completed(_ctx(db, records=records), _payload())

    section = await skill.on_song_viewed(_ctx(db), {"player_id": "p1", "song_title": "B.B.K.K.B.K.K.", "difficulty": None})
    none = await skill.on_song_viewed(_ctx(db), {"player_id": "p1", "song_title": "Nothing", "difficulty": None})

    assert section is not None
    assert [e["song_title"] for e in section.data["events"]] == ["B.B.K.K.B.K.K."]
    assert "SSS 첫 달성" in section.display.content
    assert none is None


async def test_history_with_no_records_says_so(skill: ModuleType, db: aiosqlite.Connection) -> None:
    result = await skill.history(_ctx(db), kind="rank_first")

    assert result.data == {"events": []}
    assert result.error is None
    assert result.table is None and result.display is not None


async def test_history_rejects_an_unknown_kind(skill: ModuleType, db: aiosqlite.Connection) -> None:
    result = await skill.history(_ctx(db), kind="nonsense")

    assert result.error is not None


async def test_history_only_shows_the_players_own_records(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await db.execute(
        "INSERT INTO yuni_bond_milestones (player_id, kind, value, occurred_on) VALUES "
        "('p2', 'rating_milestone', '17.00', '2026-10-09')"
    )

    result = await skill.history(_ctx(db), kind="rating_milestone")

    assert result.data["events"] == []


# ---------------------------------------------------------------- 약속


async def test_promise_lifecycle(skill: ModuleType, db: aiosqlite.Connection) -> None:
    saved = await skill.promise_set(_ctx(db), text="다음엔  크로노\n연습할게")
    assert saved.data["text"] == "다음엔 크로노 연습할게"

    listed = await skill.promise_list(_ctx(db))
    assert [p["text"] for p in listed.data["promises"]] == ["다음엔 크로노 연습할게"]
    table = listed.table
    assert table is not None and table.subject == "p1" and table.unit == "약속"
    [row] = table.rows
    assert row.ref == RowRef("yuni-bond.promise", saved.data["id"])
    assert not row.cell("곡").filled and row.cell("id").text == saved.data["id"]
    assert render_text(table).content.splitlines()[-1].startswith("1. 다음엔 크로노 연습할게  (")
    assert saved.data["id"] not in render_text(table).content  # id는 슬래시 출력에만
    assert saved.data["id"] in render_text(table, slash=True).content

    closed = await skill.promise_close(_ctx(db), promise_id=saved.data["id"])
    assert closed.error is None
    assert (await skill.promise_list(_ctx(db))).data["promises"] == []


async def test_the_same_open_promise_is_not_saved_twice(skill: ModuleType, db: aiosqlite.Connection) -> None:
    first = await skill.promise_set(_ctx(db), text="크로노 연습")
    second = await skill.promise_set(_ctx(db), text="크로노 연습")

    assert second.data["id"] == first.data["id"] and second.data["already_saved"] is True
    assert len(await _rows(db, "SELECT 1 FROM yuni_bond_promises")) == 1


async def test_open_promises_are_capped(skill: ModuleType, db: aiosqlite.Connection) -> None:
    for index in range(10):
        assert (await skill.promise_set(_ctx(db), text=f"약속 {index}")).error is None

    assert (await skill.promise_set(_ctx(db), text="하나 더")).error is not None


async def test_promise_tools_only_touch_the_players_own_promises(skill: ModuleType, db: aiosqlite.Connection) -> None:
    saved = await skill.promise_set(_ctx(db, player_id="p1"), text="내 약속")

    assert (await skill.promise_close(_ctx(db, player_id="p2"), promise_id=saved.data["id"])).error is not None
    assert (await skill.promise_list(_ctx(db, player_id="p2"))).data["promises"] == []


async def test_dropping_a_promise_is_not_the_same_as_keeping_it(skill: ModuleType, db: aiosqlite.Connection) -> None:
    saved = await skill.promise_set(_ctx(db), text="연습")
    await skill.promise_close(_ctx(db), promise_id=saved.data["id"], kept=False)

    assert [r["status"] for r in await _rows(db, "SELECT status FROM yuni_bond_promises")] == ["dropped"]


async def test_a_promise_closes_when_its_song_is_updated_in_a_sync(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = SongDbService()
    song = await songdb.resolve("B.B.K.K.B.K.K.", _ctx(db))
    await skill.promise_set(_ctx(db), text="BBKKBKK 연습", song=song)
    await skill.promise_set(_ctx(db), text="다른 곡 연습")
    records = _FakeRecords(diff=_diff([_update(old_rank="S", new_rank="SS", new_score=990_000)]))

    section = await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert section is not None and section.data["kept_promises"] == ["BBKKBKK 연습"]
    statuses = {r["text"]: r["status"] for r in await _rows(db, "SELECT text, status FROM yuni_bond_promises")}
    assert statuses == {"BBKKBKK 연습": "done", "다른 곡 연습": "open"}


async def test_old_promises_expire_at_the_next_sync(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await db.execute(
        "INSERT INTO yuni_bond_promises (id, player_id, text, created_on) VALUES ('old', 'p1', '옛 약속', '2026-09-01')"
    )
    await db.execute(
        "INSERT INTO yuni_bond_promises (id, player_id, text, created_on) VALUES ('new', 'p1', '새 약속', '2026-10-10')"
    )
    records = _FakeRecords(diff=_diff([_update(old_rank="S", new_rank="SS", new_score=990_000)]))

    await skill.on_sync_completed(_ctx(db, records=records), _payload())

    statuses = {r["id"]: r["status"] for r in await _rows(db, "SELECT id, status FROM yuni_bond_promises")}
    assert statuses == {"old": "expired", "new": "open"}


# ---------------------------------------------------------------- 프롬프트 조각


async def test_prompt_context_is_empty_without_promises(skill: ModuleType, db: aiosqlite.Connection) -> None:
    assert await skill.open_promises(_ctx(db)) is None


async def test_prompt_context_lists_recent_open_promises_of_this_player_only(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    await skill.promise_set(_ctx(db, player_id="p1"), text="내 약속")
    await skill.promise_set(_ctx(db, player_id="p2"), text="남의 약속")

    text = await skill.open_promises(_ctx(db, player_id="p1"))

    assert text is not None and "내 약속" in text and "남의 약속" not in text


async def test_prompt_context_never_exceeds_its_length_cap(skill: ModuleType, db: aiosqlite.Connection) -> None:
    for index in range(5):
        await skill.promise_set(_ctx(db), text=f"{index}" + "가" * 200)

    text = await skill.open_promises(_ctx(db))

    assert text is not None
    assert len(text) <= 300
    assert text.count("\n- ") <= 3


async def test_prompt_context_skips_expired_and_closed_promises(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await db.execute(
        "INSERT INTO yuni_bond_promises (id, player_id, text, created_on) VALUES ('old', 'p1', '옛 약속', '2026-09-01')"
    )
    saved = await skill.promise_set(_ctx(db), text="닫을 약속")
    await skill.promise_close(_ctx(db), promise_id=saved.data["id"])

    assert await skill.open_promises(_ctx(db)) is None


# ---------------------------------------------------------------- 오늘 계획


def _plan_sections() -> list[Section]:
    return [
        Section(
            order=20, display=Display(content="투두"),
            data={"kind": "goal_plan", "todos": [
                {"goal_id": "1", "song_title": "B.B.K.K.B.K.K.", "difficulty": "MAS", "target_score": 1_007_500,
                 "lamp": None, "remaining_score": 1_200},
                {"goal_id": "2", "song_title": "Other", "difficulty": "EXP", "target_score": None,
                 "lamp": "all_justice", "remaining_score": None},
            ]},
        ),
        Section(
            order=20, display=Display(content="메모"),
            data={"kind": "plan_notes", "notes": [
                {"song_title": "B.B.K.K.B.K.K.", "difficulty": "MAS", "body": "후반 왼손 주의"},
            ]},
        ),
    ]


async def test_plan_collects_candidates_with_notes_and_chart_links(skill: ModuleType, db: aiosqlite.Connection) -> None:
    bus = _GatherBus(_plan_sections())
    arcade = Arcade(id="a1", guild_id="g1", name="게임플라자", created_by="p1", created_at="2026-01-01")

    result = await skill.plan(_ctx(db, bus=bus), count=5, arcade=arcade)

    assert bus.calls == [("chunithm.plan.requested", {"player_id": "p1", "count": 5, "arcade_id": "a1"})]
    first, second = result.data["candidates"]
    assert first["note"] == "후반 왼손 주의" and first["url"].startswith("http")
    assert second["lamp"] == "all_justice" and "note" not in second
    table = result.table
    assert table is not None and table.subject == "p1" and table.unit == "채보"
    assert [c.label for c in table.columns] == ["곡", "난이도", "목표", "남은 점수", "메모", "채보 링크"]
    row, lamp_row = table.rows
    assert row.ref.kind == "chunithm.chart" and row.cell("남은 점수").raw == 1_200
    assert row.cell("메모").text == "후반 왼손 주의" and row.cell("채보 링크").text.startswith("http")
    assert not lamp_row.cell("남은 점수").filled and not lamp_row.cell("메모").filled
    lines = render_text(table).content.splitlines()
    assert lines[-2].startswith("1. B.B.K.K.B.K.K. MAS  1,007,500점까지 1,200점  메모 후반 왼손 주의  채보 링크 http")
    assert "남은 점수" not in lines[-2]  # 목표 칸의 글에 이미 있다
    assert lines[-1] == "2. Other EXP  올 저스티스 목표"
    assert "게임플라자" in table.steps[0]


async def test_plan_puts_other_skills_sections_in_the_summary(skill: ModuleType, db: aiosqlite.Connection) -> None:
    extra = Section(order=30, display=Display(content="오늘은 비가 와"), data={"kind": "weather"})
    result = await skill.plan(_ctx(db, bus=_GatherBus([*_plan_sections(), extra])), count=5)

    assert result.table is not None
    assert result.table.summary["덧붙임"] == "오늘은 비가 와"
    assert "덧붙임 오늘은 비가 와" in render_text(result.table).content


async def test_plan_with_only_other_sections_shows_them_without_a_table(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    extra = Section(order=30, display=Display(content="오늘은 비가 와"), data={"kind": "weather"})
    result = await skill.plan(_ctx(db, bus=_GatherBus([extra])))

    assert result.table is None and result.display is not None and result.display.content == "오늘은 비가 와"


async def test_plan_trims_to_the_requested_count(skill: ModuleType, db: aiosqlite.Connection) -> None:
    result = await skill.plan(_ctx(db, bus=_GatherBus(_plan_sections())), count=1)

    assert len(result.data["candidates"]) == 1
    assert result.data["total_candidates"] == 2


async def test_plan_with_no_candidates_says_so_without_failing(skill: ModuleType, db: aiosqlite.Connection) -> None:
    result = await skill.plan(_ctx(db, bus=_GatherBus([])))

    assert result.error is None
    assert result.data["candidates"] == []
    assert "후보가 아직 없어" in result.display.content
