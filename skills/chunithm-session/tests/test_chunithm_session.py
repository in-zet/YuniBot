# skills/chunithm-session/tests/test_chunithm_session.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
from core.tool_types import ToolContext
from services.chunithm.records import PbRow, SyncLogSummary
from services.common.arcades import Arcade, ArcadeVisit
from services.common.players import Player, PlayersService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_CORE_PLAYERS_SQL = Path("migrations/core/001_players.sql").read_text()
_SESSION_SQL = Path(__file__).parent.parent / "migrations" / "001_init.sql"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_session_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


@pytest.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_CORE_PLAYERS_SQL)
    await conn.executescript(_SESSION_SQL.read_text())
    try:
        yield conn
    finally:
        await conn.close()


# ---------------------------------------------------------------- 가짜 서비스


@dataclass(slots=True, kw_only=True)
class _FakeRecords:
    pbs: dict[tuple[str, str, str], PbRow] = field(default_factory=dict)
    summaries: dict[str, SyncLogSummary] = field(default_factory=dict)

    async def pb_row(self, player_id: str, *, song_title: str, difficulty: str) -> PbRow | None:
        return self.pbs.get((player_id, song_title, difficulty))

    async def latest_sync_summary(self, player_id: str) -> SyncLogSummary | None:
        return self.summaries.get(player_id)


@dataclass(slots=True, kw_only=True)
class _FakeArcades:
    visits: dict[str, list[ArcadeVisit]] = field(default_factory=dict)
    arcades: dict[str, Arcade] = field(default_factory=dict)
    recorded: list[tuple[str, str]] = field(default_factory=list)  # (arcade_id, player_id)

    async def visits_for(self, *, player_id: str, game: str) -> list[ArcadeVisit]:
        return list(self.visits.get(player_id, []))

    async def get(self, arcade_id: str) -> Arcade | None:
        return self.arcades.get(arcade_id)

    async def record_visit(self, arcade_id: str, *, player_id: str, game: str) -> None:
        self.recorded.append((arcade_id, player_id))
        self.visits.setdefault(player_id, []).append(
            ArcadeVisit(arcade_id=arcade_id, player_id=player_id, game=game, visited_at="2026-10-05T12:00:00+00:00")
        )


def _ctx(
    *, player_id: str, db: aiosqlite.Connection,
    records: _FakeRecords | None = None, arcades: _FakeArcades | None = None,
    players: PlayersService | None = None,
) -> ToolContext:
    services: dict[str, object] = {
        "records": records or _FakeRecords(), "arcades": arcades or _FakeArcades(),
        "players": players or PlayersService(db),
    }
    return ToolContext(player_id=player_id, tags=frozenset(), services=services, db=db, event_bus=EventBus())


def _sync_report_section(
    *, session_id: str, play_date: str, estimated: bool = False, current_rating: str = "16.50",
    chart_updates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "sync_report",
        "current": {"computed_rating": current_rating},
        "sessions": [
            {"id": session_id, "play_date": play_date, "estimated": estimated, "updated_count": 1, "unchanged_count": 0}
        ],
        "chart_updates": chart_updates or [],
    }


def _reported_payload(
    *, player_id: str, session_id: str = "s1", play_date: str = "2026-10-05",
    estimated: bool = False, has_new_plays: bool = True, arcade_id: str | None = None,
    sections_data: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "player_id": player_id,
        "sync_id": "sync1",
        "sessions": [{"id": session_id, "play_date": play_date, "estimated": estimated}] if has_new_plays else [],
        "has_new_plays": has_new_plays,
        "is_first_sync": False,
        "focus": None,
        "previous_synced_at": "2026-10-04T00:00:00+00:00",
        "arcade_id": arcade_id,
        "rendered_text": f"세션 요약 {play_date}",
        "sections_data": sections_data or [_sync_report_section(session_id=session_id, play_date=play_date, estimated=estimated)],
        "yuni_line": "오늘도 고생했어",
        "sent_location": {"kind": "channel", "channel_id": None},
    }


# ---------------------------------------------------------------- on_sync_reported
# (직접 player_id 문자열을 쓴다 — players 조회를 거치지 않는 경로라 가짜 id로도 된다)


async def test_on_sync_reported_skips_when_no_new_plays(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, player_id="p1")
    await skill.on_sync_reported(ctx, _reported_payload(player_id="p1", has_new_plays=False))

    row = await (await db.execute("SELECT COUNT(*) AS n FROM session_reports")).fetchone()
    assert row is not None
    assert row["n"] == 0


async def test_on_sync_reported_stores_pinned_report(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, player_id="p1")
    await skill.on_sync_reported(ctx, _reported_payload(player_id="p1"))

    row = await (await db.execute("SELECT * FROM session_reports WHERE session_id = ?", ("s1",))).fetchone()
    assert row is not None
    assert row["play_date"] == "2026-10-05"
    assert row["rendered_text"] == "세션 요약 2026-10-05"
    assert row["yuni_line"] == "오늘도 고생했어"


async def test_on_sync_reported_same_session_overwrites(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, player_id="p1")
    await skill.on_sync_reported(ctx, _reported_payload(player_id="p1"))
    second_payload = _reported_payload(player_id="p1")
    second_payload["rendered_text"] = "두 번째 리포트"
    await skill.on_sync_reported(ctx, second_payload)

    rows = list(await (await db.execute("SELECT * FROM session_reports WHERE session_id = ?", ("s1",))).fetchall())
    assert len(rows) == 1
    assert rows[0]["rendered_text"] == "두 번째 리포트"



# ---------------------------------------------------------------- list_sessions / get_session
# (본인 조회 경로는 _target_player가 ctx.players.get_player(ctx.player_id)를 실제로 불러,
# players 테이블에 행이 있는 플레이어로 호출해야 한다.)


async def _me(db: aiosqlite.Connection) -> tuple[Player, PlayersService]:
    players = PlayersService(db)
    me = await players.create_player()
    return me, players


async def test_get_session_returns_pinned_report_unchanged_by_current_records(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    me, players = await _me(db)
    ctx = _ctx(db=db, player_id=me.id, players=players)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=me.id))

    records = _FakeRecords(summaries={me.id: SyncLogSummary(
        nickname="유니", computed_rating=Decimal("17.20"), best30_average=Decimal("17.0"),
        new20_average=Decimal("17.5"), last_synced_at="2026-10-06T00:00:00+00:00",
    )})
    ctx2 = _ctx(db=db, player_id=me.id, players=players, records=records)
    result = await skill.get_session(ctx2, date="2026-10-05")

    assert result.error is None
    assert result.data["rendered_text"] == "세션 요약 2026-10-05"
    assert result.data["changes"]["rating_then"] == "16.50"
    assert result.data["changes"]["rating_now"] == "17.20"


async def test_get_session_no_date_returns_latest(skill: ModuleType, db: aiosqlite.Connection) -> None:
    me, players = await _me(db)
    ctx = _ctx(db=db, player_id=me.id, players=players)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=me.id, session_id="s1", play_date="2026-10-01"))
    await skill.on_sync_reported(ctx, _reported_payload(player_id=me.id, session_id="s2", play_date="2026-10-05"))

    result = await skill.get_session(ctx)
    assert result.error is None
    assert result.data["play_date"] == "2026-10-05"


async def test_get_session_missing_date_suggests_nearby(skill: ModuleType, db: aiosqlite.Connection) -> None:
    me, players = await _me(db)
    ctx = _ctx(db=db, player_id=me.id, players=players)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=me.id, session_id="s1", play_date="2026-10-01"))
    await skill.on_sync_reported(ctx, _reported_payload(player_id=me.id, session_id="s2", play_date="2026-10-10"))

    result = await skill.get_session(ctx, date="2026-10-05")
    assert result.error is not None
    assert result.data["nearby"] == {"before": "2026-10-01", "after": "2026-10-10"}


async def test_get_session_no_sessions_at_all(skill: ModuleType, db: aiosqlite.Connection) -> None:
    me, players = await _me(db)
    ctx = _ctx(db=db, player_id=me.id, players=players)
    result = await skill.get_session(ctx)
    assert result.error == skill._NOT_SYNCED_ERROR


async def test_get_session_detects_chart_change_since_report(skill: ModuleType, db: aiosqlite.Connection) -> None:
    me, players = await _me(db)
    ctx = _ctx(db=db, player_id=me.id, players=players)
    updates = [
        {
            "song_title": "곡A", "difficulty": "MAS", "old_score": None, "new_score": 1_000_000,
            "old_rank": None, "new_rank": "SS+", "old_rating": None, "new_rating": "16.50",
            "session_id": "s1", "estimated_session": False, "entered_frame": False,
        }
    ]
    await skill.on_sync_reported(
        ctx, _reported_payload(player_id=me.id, sections_data=[_sync_report_section(
            session_id="s1", play_date="2026-10-05", chart_updates=updates,
        )]),
    )

    records = _FakeRecords(pbs={(me.id, "곡A", "MAS"): PbRow(
        song_title="곡A", difficulty="MAS", score=1_007_500, rank="SSS", clear_lamp="CLEAR",
        combo_lamp="NONE", chain_lamp="NONE", const=14.0, rating=Decimal("16.80"),
        session_id="s1", updated_at="2026-10-06T00:00:00+00:00",
    )})
    ctx2 = _ctx(db=db, player_id=me.id, players=players, records=records)
    result = await skill.get_session(ctx2, date="2026-10-05")

    assert result.error is None
    change = result.data["changes"]["chart_changes"][0]
    assert change["then_rank"] == "SS+"
    assert change["now_rank"] == "SSS"


async def test_get_session_arcade_filter_no_match_lists_visited_arcades(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    me, players = await _me(db)
    ctx = _ctx(db=db, player_id=me.id, players=players)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=me.id))

    arcades = _FakeArcades(arcades={"ar1": Arcade(id="ar1", guild_id="g1", name="겜플", created_by=me.id, created_at="x")})
    ctx2 = _ctx(db=db, player_id=me.id, players=players, arcades=arcades)
    result = await skill.get_session(ctx2, arcade=arcades.arcades["ar1"])

    assert result.error is not None
    assert result.data["visited_arcades"] == []


async def test_get_session_filters_by_arcade(skill: ModuleType, db: aiosqlite.Connection) -> None:
    """방문 기록은 chunithm-arcade의 on_sync_reported가 남긴다(이 스킬은 더 이상 기록하지
    않는다) — 여기서는 이미 방문이 있다고 가정하고 arcades.record_visit()으로 직접 심는다."""
    me, players = await _me(db)
    arcades = _FakeArcades(arcades={"ar1": Arcade(id="ar1", guild_id="g1", name="겜플", created_by=me.id, created_at="x")})
    ctx = _ctx(db=db, player_id=me.id, players=players, arcades=arcades)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=me.id))
    await arcades.record_visit("ar1", player_id=me.id, game="chunithm")

    result = await skill.get_session(ctx, arcade=arcades.arcades["ar1"])
    assert result.error is None
    assert result.data["arcade"] == "겜플"


async def test_list_sessions_filters_by_period(skill: ModuleType, db: aiosqlite.Connection) -> None:
    me, players = await _me(db)
    ctx = _ctx(db=db, player_id=me.id, players=players)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=me.id, session_id="s1", play_date="2026-09-20"))
    await skill.on_sync_reported(ctx, _reported_payload(player_id=me.id, session_id="s2", play_date="2026-10-05"))

    result = await skill.list_sessions(ctx, start="2026-10-01", end="2026-10-31")
    assert [s["play_date"] for s in result.data["sessions"]] == ["2026-10-05"]


async def test_list_sessions_empty_when_no_match(skill: ModuleType, db: aiosqlite.Connection) -> None:
    me, players = await _me(db)
    ctx = _ctx(db=db, player_id=me.id, players=players)
    result = await skill.list_sessions(ctx, start="2026-01-01", end="2026-01-31")
    assert result.data["sessions"] == []


# ---------------------------------------------------------------- 공개 설정


async def test_others_private_records_are_hidden(skill: ModuleType, db: aiosqlite.Connection) -> None:
    players = PlayersService(db)
    other = await players.create_player()
    assert other.records_public is False
    ctx_owner = _ctx(db=db, player_id=other.id, players=players)
    await skill.on_sync_reported(ctx_owner, _reported_payload(player_id=other.id))

    me = await players.create_player()
    ctx_viewer = _ctx(db=db, player_id=me.id, players=players)
    result = await skill.get_session(ctx_viewer, player=other)
    assert result.error == skill._PRIVATE_ERROR


async def test_others_public_records_are_visible(skill: ModuleType, db: aiosqlite.Connection) -> None:
    players = PlayersService(db)
    other = await players.create_player()
    await players.set_visibility(other.id, "records_public", True)
    other = await players.get_player(other.id)
    assert other is not None
    ctx_owner = _ctx(db=db, player_id=other.id, players=players)
    await skill.on_sync_reported(ctx_owner, _reported_payload(player_id=other.id))

    me = await players.create_player()
    ctx_viewer = _ctx(db=db, player_id=me.id, players=players)
    result = await skill.get_session(ctx_viewer, player=other)
    assert result.error is None


# ---------------------------------------------------------------- delete_session
# (ctx.player_id로만 지우는 경로라 players 조회를 거치지 않는다.)


async def test_delete_session_requires_target(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, player_id="p1")
    result = await skill.delete_session(ctx)
    assert result.error is not None


async def test_delete_session_removes_own_row(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, player_id="p1")
    await skill.on_sync_reported(ctx, _reported_payload(player_id="p1"))

    result = await skill.delete_session(ctx, date="2026-10-05")
    assert result.error is None
    assert result.data["deleted_count"] == 1

    row = await (await db.execute("SELECT * FROM session_reports WHERE session_id = ?", ("s1",))).fetchone()
    assert row is None


async def test_delete_session_does_not_touch_others(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx_p1 = _ctx(db=db, player_id="p1")
    await skill.on_sync_reported(ctx_p1, _reported_payload(player_id="p1", session_id="s1"))
    ctx_p2 = _ctx(db=db, player_id="p2")
    await skill.on_sync_reported(ctx_p2, _reported_payload(player_id="p2", session_id="s2"))

    result = await skill.delete_session(ctx_p1, date="2026-10-05")
    assert result.error is None
    assert result.data["deleted_count"] == 1

    row = await (await db.execute("SELECT * FROM session_reports WHERE session_id = ?", ("s2",))).fetchone()
    assert row is not None
