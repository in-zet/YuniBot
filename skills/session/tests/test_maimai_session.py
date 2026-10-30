# skills/session/tests/test_maimai_session.py
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

from core.events import EventBus, Section
from core.tool_types import ToolContext
from services.common.players import PlayersService
from services.game.records import PbRow, SyncLogSummary

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_CORE_PLAYERS_SQL = Path("migrations/core/001_players.sql").read_text()
_MIGRATIONS = sorted((Path(__file__).parent.parent / "migrations").glob("*.sql"))


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_maimai_session_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    module = _load_skill()
    module._ASK_DELAY_SECONDS = 0.0  # pyright: ignore[reportAttributeAccessIssue]
    return module


@pytest.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_CORE_PLAYERS_SQL)
    for migration in _MIGRATIONS:
        await conn.executescript(migration.read_text())
    try:
        yield conn
    finally:
        await conn.close()


@dataclass(slots=True, kw_only=True)
class _FakeRecords:
    pbs: dict[tuple[str, str, str, str], PbRow] = field(default_factory=dict)
    summary: SyncLogSummary | None = None

    async def pb_row(self, player_id: str, *, song_title: str, difficulty: str, chart_type: str = "") -> PbRow | None:
        return self.pbs.get((song_title, chart_type, difficulty, player_id))

    async def latest_sync_summary(self, player_id: str) -> SyncLogSummary | None:
        return self.summary


@dataclass(slots=True, kw_only=True)
class _FakeArcades:
    async def visits_for(self, *, player_id: str, game: str) -> list[Any]:
        return []


@dataclass(slots=True, kw_only=True)
class _FakeSettings:
    async def get_value(self, *, scope: str, scope_id: str, skill: str, name: str, default: Any) -> Any:
        return False  # 후기 물어보기 꺼짐


class _RecordingBus(EventBus):
    def __init__(self) -> None:
        super().__init__()
        self.gathered: list[tuple[str, str | None]] = []

    async def emit_gather(  # type: ignore[override]
        self, event_name: str, payload: Any, *, player_id: str, guild_id: str | None, game: str | None = None
    ) -> list[Section]:
        self.gathered.append((event_name, game))
        return []


def _ctx(db: aiosqlite.Connection, player_id: str, game: str, *, records: _FakeRecords | None = None,
         bus: EventBus | None = None) -> ToolContext:
    services = {
        "records": records or _FakeRecords(), "arcades": _FakeArcades(), "players": PlayersService(db),
        "settings": _FakeSettings(),
    }
    return ToolContext(player_id=player_id, tags=frozenset(), services=services, db=db, event_bus=bus or EventBus(), game=game)


def _payload(player_id: str, session_id: str, play_date: str, *, chart_updates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "player_id": player_id, "sync_id": "sync1", "has_new_plays": True,
        "sessions": [{"id": session_id, "play_date": play_date, "estimated": False}],
        "rendered_text": f"maimai 세션 {play_date}", "yuni_line": "",
        "sections_data": [{
            "kind": "sync_report", "current": {"computed_rating": "12000"},
            "sessions": [{"id": session_id, "play_date": play_date, "estimated": False, "updated_count": 1}],
            "chart_updates": chart_updates or [],
        }],
    }


async def test_maimai_report_is_stored_and_listed_per_game(skill, db) -> None:
    me = await PlayersService(db).create_player()
    maimai_ctx = _ctx(db, me.id, "maimai")
    await skill.on_maimai_sync_reported(maimai_ctx, _payload(me.id, "m1", "2026-10-05"))

    row = await (await db.execute("SELECT game, session_id FROM session_reports")).fetchone()
    assert row is not None and (row["game"], row["session_id"]) == ("maimai", "m1")
    mine = await skill.list_sessions(maimai_ctx)
    other = await skill.list_sessions(_ctx(db, me.id, "chunithm"))
    assert mine.error is None and mine.table is not None and mine.table.row_count == 1
    assert other.table is None  # 츄니즘 세션은 아직 없다


async def test_get_session_compares_with_the_maimai_chart_and_integer_rating(skill, db) -> None:
    me = await PlayersService(db).create_player()
    update = {
        "session_id": "m1", "song_title": "Fresh", "chart_type": "DX", "difficulty": "MAS",
        "new_rank": "SSS", "new_score": 1_000_000, "new_rating": "322",
    }
    await skill.on_maimai_sync_reported(_ctx(db, me.id, "maimai"), _payload(me.id, "m1", "2026-10-05", chart_updates=[update]))
    now = PbRow(
        song_title="Fresh", chart_type="DX", difficulty="MAS", score=1_005_000, rank="SSS+", clear_lamp="",
        combo_lamp="", chain_lamp="", const=14.9, rating=Decimal(335), session_id=None, updated_at="2026-10-06",
    )
    records = _FakeRecords(
        pbs={("Fresh", "DX", "MAS", me.id): now},
        summary=SyncLogSummary(
            nickname="유니", computed_rating=Decimal(13500), best30_average=Decimal(0), new20_average=Decimal(0),
            last_synced_at="2026-10-06T00:00:00+00:00",
        ),
    )
    bus = _RecordingBus()

    result = await skill.get_session(_ctx(db, me.id, "maimai", records=records, bus=bus), date="2026-10-05")

    assert result.error is None
    assert result.data["changes"]["rating_now"] == "13500"
    [change] = result.data["changes"]["chart_changes"]
    assert (change["chart_type"], change["now_rank"], change["now_rating"]) == ("DX", "SSS+", "335")
    assert bus.gathered == [("maimai.session.viewed", "maimai")]
