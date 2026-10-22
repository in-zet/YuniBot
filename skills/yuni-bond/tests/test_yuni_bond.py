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
from core.tables import RowRef, TableBuilder, WorkTable, cell, render_text
from core.tool_types import Display, ToolContext
from services.chunithm.records import (
    ChartUpdate,
    LampChange,
    PbRow,
    RatingDeltas,
    SessionInfo,
    SyncDiff,
)
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
    def __init__(
        self, *, diff: SyncDiff | None = None, deltas: RatingDeltas | None = None, pb_rows: list[PbRow] | None = None
    ) -> None:
        self.diff = diff
        self.deltas = deltas
        self.pb_rows = pb_rows or []

    async def all_pb_rows(self, player_id: str) -> list[PbRow]:
        return self.pb_rows

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
    old_lamp: str | None = None, new_lamp: str = "",
) -> ChartUpdate:
    return ChartUpdate(
        song_title=title, difficulty=difficulty, old_score=old_score, new_score=new_score, old_rank=old_rank,
        new_rank=new_rank, old_rating=None, new_rating=None, session_id=session_id, estimated_session=False,
        entered_frame=False, old_combo_lamp=old_lamp, new_combo_lamp=new_lamp,
    )


def _diff(
    updates: list[ChartUpdate], *, play_date: str = "2026-10-18", lamp_changes: list[LampChange] | None = None
) -> SyncDiff:
    return SyncDiff(
        sync_id="sync1", sessions=[SessionInfo(id="s1", play_date=play_date, estimated=False)], chart_updates=updates,
        unchanged_plays=[], frame_exits=[], previous_computed_rating=None, previous_best30_average=None,
        previous_new20_average=None, lamp_changes=lamp_changes or [],
    )


def _pb(
    title: str = "B.B.K.K.B.K.K.", difficulty: str = "MAS", *, const: float | None = 15.4, rank: str = "S",
    lamp: str = "NONE",
) -> PbRow:
    """동기화 뒤(지금)의 PB 행. 이번에 바뀐 채보는 옛 값이 `ChartUpdate`·`LampChange`에 있다."""
    return PbRow(
        song_title=title, difficulty=difficulty, score=1_000_000, rank=rank, clear_lamp="CLEAR", combo_lamp=lamp,
        chain_lamp="NONE", const=const, rating=None, session_id=None, updated_at="2026-10-18T00:00:00+00:00",
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
    assert table is not None and table.subject == "p1" and table.unit == "이정표"
    [row] = table.rows
    assert row.ref.kind == "yuni-bond.event" and row.ref.key == '["goal_done", "2026-10-11", "", ""]'
    assert (row.cell("날짜").raw, row.cell("날짜").text) == ("2026-10-11", "10월 11일")
    assert (row.cell("종류").raw, row.cell("종류").text) == ("goal_done", "To-Do 달성")
    assert not row.cell("곡").filled  # 곡과 무관한 기록은 곡 칸이 빈다
    assert row.cell("내용").text == "크로노 MAS SSS · 10일 걸림"


# ---------------------------------------------------------------- 동기화


async def _const_firsts(db: aiosqlite.Connection) -> list[tuple[str, str, str]]:
    rows = await _rows(
        db, "SELECT label, value, song_title FROM yuni_bond_milestones WHERE kind = 'const_first' ORDER BY id"
    )
    return [(r["label"], r["value"], r["song_title"]) for r in rows]


async def test_const_first_is_recorded_with_the_time_it_took(skill: ModuleType, db: aiosqlite.Connection) -> None:
    first = _FakeRecords(
        diff=_diff([_update(old_rank="SS+", new_rank="SS+", new_score=1_005_000)], play_date="2026-10-01"),
        pb_rows=[_pb(rank="SS+")],
    )
    await skill.on_sync_completed(_ctx(db, records=first), _payload())

    later = _FakeRecords(
        diff=_diff([_update(old_rank="SS+", new_rank="SSS")], play_date="2026-10-18"), pb_rows=[_pb(rank="SSS")]
    )
    section = await skill.on_sync_completed(_ctx(db, records=later), _payload())

    assert section is not None
    assert section.display is None
    assert section.data["first_achievements"] == [
        {"song_title": "B.B.K.K.B.K.K.", "difficulty": "MAS", "const": "15.4", "tier": "트리플", "days_on_chart": 17}
    ]
    assert "15.4 트리플 1호" in (section.hint or "")
    assert await _const_firsts(db) == [("15.4", "SSS", "B.B.K.K.B.K.K.")]


async def test_a_rank_already_held_is_not_a_first(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(
        diff=_diff([_update(old_rank="SSS", new_rank="SSS", new_score=1_008_000)]), pb_rows=[_pb(rank="SSS")]
    )

    section = await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert section is None
    assert await _const_firsts(db) == []


async def test_reaching_sss_plus_in_one_go_records_both_tiers(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(
        diff=_diff([_update(old_rank="SS+", new_rank="SSS+", new_score=1_009_000)]), pb_rows=[_pb(rank="SSS+")]
    )
    await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert sorted(await _const_firsts(db)) == [("15.4", "SSS", "B.B.K.K.B.K.K."), ("15.4", "SSS+", "B.B.K.K.B.K.K.")]


async def test_another_chart_of_the_same_const_that_already_had_it_means_no_first(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """같은 상수의 다른 채보가 동기화 전에 이미 트리플이었다 — 첫 동기화 이전부터 있던 달성도 센다."""
    records = _FakeRecords(
        diff=_diff([_update(old_rank="SS+", new_rank="SSS")]),
        pb_rows=[_pb(rank="SSS"), _pb("Other", "EXP", rank="SSS")],
    )

    assert await skill.on_sync_completed(_ctx(db, records=records), _payload()) is None
    assert await _const_firsts(db) == []


async def test_each_const_has_its_own_first(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(
        diff=_diff([_update(old_rank="SS+", new_rank="SSS"), _update("Other", "EXP", old_rank="SS+", new_rank="SSS")]),
        pb_rows=[_pb(rank="SSS", const=15.4), _pb("Other", "EXP", rank="SSS", const=15.2)],
    )
    await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert sorted(await _const_firsts(db)) == [("15.2", "SSS", "Other"), ("15.4", "SSS", "B.B.K.K.B.K.K.")]


async def test_two_charts_of_one_const_in_one_sync_only_the_earlier_is_the_first(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    diff = SyncDiff(
        sync_id="sync1",
        sessions=[SessionInfo(id="s1", play_date="2026-10-17", estimated=False), SessionInfo(id="s2", play_date="2026-10-18", estimated=False)],
        chart_updates=[
            _update("Later", "MAS", old_rank="SS+", new_rank="SSS", session_id="s2"),
            _update("Earlier", "MAS", old_rank="SS+", new_rank="SSS", session_id="s1"),
        ],
        unchanged_plays=[], frame_exits=[], previous_computed_rating=None, previous_best30_average=None,
        previous_new20_average=None,
    )
    records = _FakeRecords(diff=diff, pb_rows=[_pb("Later", rank="SSS"), _pb("Earlier", rank="SSS")])

    await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert await _const_firsts(db) == [("15.4", "SSS", "Earlier")]


async def test_charts_with_an_unknown_const_are_not_counted(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(
        diff=_diff([_update(old_rank="SS+", new_rank="SSS")]), pb_rows=[_pb(rank="SSS", const=None)]
    )

    assert await skill.on_sync_completed(_ctx(db, records=records), _payload()) is None
    assert await _const_firsts(db) == []


async def test_full_combo_and_all_justice_firsts_come_from_lamp_changes(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    records = _FakeRecords(
        diff=_diff(
            [],
            lamp_changes=[
                LampChange(
                    song_title="B.B.K.K.B.K.K.", difficulty="MAS", old_combo_lamp="NONE", new_combo_lamp="ALL JUSTICE",
                    session_id=None,
                )
            ],
        ),
        pb_rows=[_pb(rank="S", lamp="ALL JUSTICE")],
    )

    section = await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert sorted(await _const_firsts(db)) == [("15.4", "AJ", "B.B.K.K.B.K.K."), ("15.4", "FC", "B.B.K.K.B.K.K.")]
    assert section is not None and "풀콤" in (section.hint or "") and "올저" in (section.hint or "")
    [row] = await _rows(db, "SELECT occurred_on FROM yuni_bond_milestones WHERE kind = 'const_first' AND value = 'AJ'")
    assert row["occurred_on"] == "2026-10-19"  # 세션을 못 찾은 램프 변화는 오늘 날짜


async def test_a_score_update_that_also_raised_the_lamp_counts_the_lamp(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    records = _FakeRecords(
        diff=_diff([_update(old_rank="S", new_rank="SS", new_score=995_000, old_lamp="NONE", new_lamp="FULL COMBO")]),
        pb_rows=[_pb(rank="SS", lamp="FULL COMBO")],
    )
    await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert await _const_firsts(db) == [("15.4", "FC", "B.B.K.K.B.K.K.")]


async def test_a_full_combo_held_by_another_chart_of_the_const_blocks_the_first(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    records = _FakeRecords(
        diff=_diff(
            [], lamp_changes=[LampChange(
                song_title="B.B.K.K.B.K.K.", difficulty="MAS", old_combo_lamp="NONE", new_combo_lamp="FULL COMBO",
                session_id=None,
            )],
        ),
        pb_rows=[_pb(lamp="FULL COMBO"), _pb("Other", "EXP", lamp="ALL JUSTICE")],  # Other는 이미 올저(=풀콤)였다
    )

    assert await skill.on_sync_completed(_ctx(db, records=records), _payload()) is None
    assert await _const_firsts(db) == []


async def test_the_same_sync_twice_records_nothing_new(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([_update(old_rank="SS+", new_rank="SSS")]), pb_rows=[_pb(rank="SSS")])
    await skill.on_sync_completed(_ctx(db, records=records), _payload())
    again = await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert again is None
    assert len(await _const_firsts(db)) == 1


async def _stars(db: aiosqlite.Connection, kind: str = "rating_star") -> list[tuple[str, str]]:
    rows = await _rows(db, "SELECT label, value FROM yuni_bond_milestones WHERE kind = ? ORDER BY id", kind)
    return [(r["label"], r["value"]) for r in rows]


async def test_rating_star_gives_the_next_star_and_remaining_amount_computed_in_code(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("16.20", "16.26"))

    section = await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert section is not None
    assert section.data["rating_milestones"] == ["무지개 2성"]
    assert section.data["next_milestone"] == "무지개 3성"
    assert section.data["remaining_to_next"] == "0.2400"
    assert "무지개 2성" in (section.hint or "")
    assert await _stars(db) == [("무지개 2성", "16.25")]
    assert await _stars(db, "rating_floor") == []  # 16.2는 직전 레이팅에 이미 닿았다


async def test_a_sync_can_record_a_star_and_a_floor_together(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("16.60", "16.80"))

    section = await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert section is not None and section.data["rating_milestones"] == ["무지개 4성", "무지개 8층"]
    assert await _stars(db) == [("무지개 4성", "16.75")]
    assert await _stars(db, "rating_floor") == [("무지개 8층", "16.80")]


async def test_below_platinum_only_the_tier_name_is_recorded(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("14.40", "14.60"))

    await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert await _stars(db) == [("금", "14.50")]
    assert await _stars(db, "rating_floor") == []


async def test_nothing_is_recorded_again_after_the_rating_drops_and_returns(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    up = _FakeRecords(diff=_diff([]), deltas=_deltas("16.90", "17.05"))
    await skill.on_sync_completed(_ctx(db, records=up), _payload())
    dropped = _FakeRecords(diff=_diff([]), deltas=_deltas("17.05", "16.40"))
    await skill.on_sync_completed(_ctx(db, records=dropped), _payload())
    again = _FakeRecords(diff=_diff([]), deltas=_deltas("16.40", "17.00"))

    assert await skill.on_sync_completed(_ctx(db, records=again), _payload()) is None
    assert [label for label, _ in await _stars(db)] == ["극무지개 1성"]


async def test_a_new_high_after_a_drop_records_only_what_is_above_the_old_peak(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    await skill.on_sync_completed(
        _ctx(db, records=_FakeRecords(diff=_diff([]), deltas=_deltas("16.90", "17.05"))), _payload()
    )
    await skill.on_sync_completed(
        _ctx(db, records=_FakeRecords(diff=_diff([]), deltas=_deltas("17.05", "16.40"))), _payload()
    )

    section = await skill.on_sync_completed(
        _ctx(db, records=_FakeRecords(diff=_diff([]), deltas=_deltas("16.40", "17.30"))), _payload()
    )

    assert section is not None and section.data["rating_milestones"] == ["극무지개 2성", "극무지개 3층"]


async def test_the_peak_is_kept_even_when_a_sync_has_no_new_plays(skill: ModuleType, db: aiosqlite.Connection) -> None:
    """새 버전에서 레이팅이 내려간 동기화에 플레이가 없어도 최고치는 남는다 — 나중에 올라와도 다시 기록하지 않는다."""
    await skill.on_sync_completed(
        _ctx(db, records=_FakeRecords(diff=_diff([]), deltas=_deltas("17.00", "17.10"))), _payload(has_new_plays=False)
    )
    await skill.on_sync_completed(
        _ctx(db, records=_FakeRecords(diff=_diff([]), deltas=_deltas("17.10", "16.20"))), _payload(has_new_plays=False)
    )

    back = _FakeRecords(diff=_diff([]), deltas=_deltas("16.20", "17.05"))
    assert await skill.on_sync_completed(_ctx(db, records=back), _payload()) is None
    assert await _stars(db) == []
    [peak] = await _rows(db, "SELECT value FROM yuni_bond_milestones WHERE kind = 'rating_peak'")
    assert peak["value"] == "17.1000"


async def test_a_higher_star_reports_the_days_since_the_previous_one(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    await db.execute(
        "INSERT INTO yuni_bond_milestones (player_id, kind, value, label, occurred_on) VALUES "
        "('p1', 'rating_star', '16.25', '무지개 2성', '2026-10-09')"
    )
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("16.45", "16.52"))

    section = await skill.on_sync_completed(_ctx(db, records=records), _payload())

    assert section is not None
    assert section.data["rating_milestones"][0] == "무지개 3성"
    assert section.data["days_since_previous_milestone"] == 10


async def test_no_hint_when_nothing_changed(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("16.20", "16.26"))

    assert await skill.on_sync_completed(_ctx(db, records=records), _payload(has_new_plays=False)) is None
    assert await _rows(db, "SELECT 1 FROM yuni_bond_milestones WHERE kind != 'rating_peak'") == []


async def test_no_hint_when_the_sync_has_nothing_to_say(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(
        diff=_diff([_update(old_rank="S", new_rank="SS", new_score=990_000)]), deltas=_deltas("16.21", "16.22"),
        pb_rows=[_pb(rank="SS")],
    )

    assert await skill.on_sync_completed(_ctx(db, records=records), _payload()) is None


async def test_an_unknown_rating_records_no_rating_milestone(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("16.20", None))

    assert await skill.on_sync_completed(_ctx(db, records=records), _payload()) is None
    assert await _rows(db, "SELECT 1 FROM yuni_bond_milestones WHERE kind LIKE 'rating%'") == []


async def test_the_hint_setting_off_still_records_but_gives_no_hint(skill: ModuleType, db: aiosqlite.Connection) -> None:
    records = _FakeRecords(diff=_diff([]), deltas=_deltas("16.20", "16.26"))
    settings = _FakeSettings({"동기화 때 덧붙이는 말": False})

    section = await skill.on_sync_completed(_ctx(db, records=records, settings=settings), _payload())

    assert section is None
    assert len(await _rows(db, "SELECT 1 FROM yuni_bond_milestones WHERE kind = 'rating_star'")) == 1


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
        diff=_diff([_update(old_rank="SS+", new_rank="SSS"), _update("Other", "EXP", old_rank="S", new_rank="SSS")]),
        pb_rows=[_pb(rank="SSS", const=15.4), _pb("Other", "EXP", rank="SSS", const=15.2)],
    )
    await skill.on_sync_completed(_ctx(db, records=records), _payload())

    section = await skill.on_song_viewed(_ctx(db), {"player_id": "p1", "song_title": "B.B.K.K.B.K.K.", "difficulty": None})
    none = await skill.on_song_viewed(_ctx(db), {"player_id": "p1", "song_title": "Nothing", "difficulty": None})

    assert section is not None
    assert [e["song_title"] for e in section.data["events"]] == ["B.B.K.K.B.K.K."]
    assert "15.4 트리플 1호" in section.display.content
    assert none is None


async def test_history_with_no_records_says_so(skill: ModuleType, db: aiosqlite.Connection) -> None:
    result = await skill.history(_ctx(db), kind="const_first")

    assert result.data == {"events": []}
    assert result.error is None
    assert result.table is None and result.display is not None


async def test_history_rejects_an_unknown_kind(skill: ModuleType, db: aiosqlite.Connection) -> None:
    result = await skill.history(_ctx(db), kind="nonsense")

    assert result.error is not None


async def test_history_only_shows_the_players_own_records(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await db.execute(
        "INSERT INTO yuni_bond_milestones (player_id, kind, value, occurred_on) VALUES "
        "('p2', 'rating_star', '17.00', '2026-10-09')"
    )

    result = await skill.history(_ctx(db), kind="rating_star")

    assert result.data["events"] == []


# ---------------------------------------------------------------- 약속


def _promise_table(*promise_ids: str) -> WorkTable:
    builder = TableBuilder(subject="p1", unit="약속")
    builder.column("약속")
    for promise_id in promise_ids:
        builder.row(RowRef("yuni-bond.promise", promise_id), {"약속": cell(promise_id)})
    return builder.build()


async def test_promise_lifecycle(skill: ModuleType, db: aiosqlite.Connection) -> None:
    saved = await skill.promise_set(_ctx(db), text="다음엔  크로노\n연습할게")
    assert saved.data["text"] == "다음엔 크로노 연습할게"

    listed = await skill.promise_list(_ctx(db))
    assert listed.data == {"count": 1}  # 약속 id는 표에만 있다
    table = listed.table
    assert table is not None and table.subject == "p1" and table.unit == "약속"
    [row] = table.rows
    assert row.ref == RowRef("yuni-bond.promise", saved.data["id"])
    assert not row.cell("곡").filled and row.cell("id").text == saved.data["id"]
    assert render_text(table).content.splitlines()[-1].startswith("1. 다음엔 크로노 연습할게  (")
    assert saved.data["id"] not in render_text(table).content  # id는 슬래시 출력에만
    assert saved.data["id"] in render_text(table, slash=True).content

    closed = await skill.close_promises(_ctx(db), listed.table)
    assert closed.error is None
    assert (await skill.promise_list(_ctx(db))).data == {"count": 0}


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

    assert (await skill.close_promises(_ctx(db, player_id="p2"), _promise_table(saved.data["id"]))).error is not None
    assert (await skill.promise_list(_ctx(db, player_id="p2"))).data == {"count": 0}


async def test_close_promises_closes_several_and_counts_only_real_changes(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    first = await skill.promise_set(_ctx(db), text="연습1")
    second = await skill.promise_set(_ctx(db), text="연습2")
    table = _promise_table(first.data["id"], second.data["id"])
    await skill.close_promises(_ctx(db), _promise_table(first.data["id"]))  # 표를 찍은 뒤 이미 닫혔다

    result = await skill.close_promises(_ctx(db), table)

    assert result.data["closed_count"] == 1 and result.data["already_closed"] == 1
    assert result.data["texts"] == ["연습2"]
    assert "1개는 이미 닫혀 있어서" in result.display.content
    assert (await skill.close_promises(_ctx(db), table)).error is not None  # 이제 열린 약속이 없다


async def test_dropping_a_promise_is_not_the_same_as_keeping_it(skill: ModuleType, db: aiosqlite.Connection) -> None:
    saved = await skill.promise_set(_ctx(db), text="연습")
    await skill.close_promises(_ctx(db), _promise_table(saved.data["id"]), kept=False)

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
    await skill.close_promises(_ctx(db), _promise_table(saved.data["id"]))

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
    arcade = Arcade(id="a1", name="게임플라자", created_by="p1", created_at="2026-01-01")

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


# ---------------------------------------------------------------- 이정표 보기


async def test_history_lists_const_firsts_and_ratings_with_the_words_of_the_player(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    await skill.on_sync_completed(
        _ctx(db, records=_FakeRecords(diff=_diff([_update(old_rank="SS+", new_rank="SS+", new_score=1_005_000)], play_date="2026-10-01"), pb_rows=[_pb(rank="SS+")])),
        _payload(),
    )
    records = _FakeRecords(
        diff=_diff([_update(old_rank="SS+", new_rank="SSS")]), deltas=_deltas("16.60", "16.80"), pb_rows=[_pb(rank="SSS")]
    )
    await skill.on_sync_completed(_ctx(db, records=records), _payload())

    result = await skill.history(_ctx(db))

    table = result.table
    assert table is not None and table.unit == "이정표"
    details = {row.cell("종류").raw: row.cell("내용").text for row in table.rows}
    assert details["const_first"] == "15.4 트리플 1호 · 17일 걸림"
    assert {row.cell("내용").text for row in table.rows if row.cell("종류").text == "레이팅 달성"} == {"무지개 4성", "무지개 8층"}
    assert "peak" not in str(result.data)  # 최고치는 바탕 자료라 보이지 않는다
