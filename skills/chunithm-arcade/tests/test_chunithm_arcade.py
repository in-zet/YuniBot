# skills/chunithm-arcade/tests/test_chunithm_arcade.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
from core.tables import render_text
from core.tool_types import ToolContext
from services.chunithm.arcade_settings import ArcadeSettings
from services.chunithm.records import SessionInfo
from services.common.arcades import ArcadesService
from services.common.players import PlayersService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_CORE_PLAYERS_SQL = Path("migrations/core/001_players.sql").read_text()
_CORE_ARCADES_SQL = Path("migrations/core/005_arcades.sql").read_text()
_ARCADE_SQL = (Path(__file__).parent.parent / "migrations" / "001_init.sql").read_text()


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_arcade_skill_under_test", _SKILL_PY)
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
    await conn.executescript(_CORE_ARCADES_SQL)
    await conn.executescript(_ARCADE_SQL)
    try:
        yield conn
    finally:
        await conn.close()


@dataclass(slots=True, kw_only=True)
class _FakeRecords:
    sessions: dict[str, SessionInfo | None] = field(default_factory=dict)

    async def latest_session(self, player_id: str) -> SessionInfo | None:
        return self.sessions.get(player_id)


@dataclass(slots=True, kw_only=True)
class _FakeChunithmNet:
    settings: ArcadeSettings | None = None
    error: Exception | None = None
    calls: int = 0

    async def get_arcade_settings(self, player_id: str, *, ctx: ToolContext | None = None) -> ArcadeSettings:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.settings is not None
        return self.settings


def _settings(**overrides: str) -> ArcadeSettings:
    named = {
        "speed": "1.00", "field_wall_position": "0", "play_timing_offset": "0.0",
        "judge_timing_offset": "0.0", "guide_sound": "5",
    }
    named.update(overrides)
    return ArcadeSettings(raw={**named, "extraField": "x"}, **named)


def _ctx(
    *, player_id: str, db: aiosqlite.Connection,
    records: _FakeRecords | None = None, chunithm_net: _FakeChunithmNet | None = None,
) -> ToolContext:
    arcades = ArcadesService(db)
    services: dict[str, object] = {
        "arcades": arcades, "records": records or _FakeRecords(), "chunithm_net": chunithm_net or _FakeChunithmNet(),
    }
    return ToolContext(player_id=player_id, tags=frozenset(), services=services, db=db, event_bus=EventBus())


async def _make_player(db: aiosqlite.Connection) -> str:
    players = PlayersService(db)
    player = await players.create_player()
    return player.id


async def _make_arcade(ctx: ToolContext, *, name: str = "겜플"):
    arcades: ArcadesService = ctx.arcades  # type: ignore[assignment]
    return await arcades.create(guild_id="g1", name=name, created_by=ctx.player_id)


# ---------------------------------------------------------------- record_visit


async def test_record_visit_without_prior_session_still_reads_settings(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    """한 번도 동기화 안 했으면(latest_session이 None) 비교할 과거가 없으니 그냥 읽는다."""
    player_id = await _make_player(db)
    chunithm_net = _FakeChunithmNet(settings=_settings(speed="2.00"))
    ctx = _ctx(player_id=player_id, db=db, chunithm_net=chunithm_net)
    arcade = await _make_arcade(ctx)

    result = await skill.record_visit(ctx, arcade=arcade)

    assert result.error is None
    assert result.data["settings_saved"] is True
    assert result.data["changed"] == []
    assert chunithm_net.calls == 1


async def test_record_visit_matching_latest_session_reads_settings(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    records = _FakeRecords(sessions={player_id: SessionInfo(id="s1", play_date="2026-10-11", estimated=False)})
    chunithm_net = _FakeChunithmNet(settings=_settings())
    ctx = _ctx(player_id=player_id, db=db, records=records, chunithm_net=chunithm_net)
    arcade = await _make_arcade(ctx)

    result = await skill.record_visit(ctx, arcade=arcade, date="2026-10-11")

    assert result.data["settings_saved"] is True
    assert result.data["play_date"] == "2026-10-11"


async def test_record_visit_on_historical_date_skips_settings(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    records = _FakeRecords(sessions={player_id: SessionInfo(id="s1", play_date="2026-10-12", estimated=False)})
    chunithm_net = _FakeChunithmNet(settings=_settings())
    ctx = _ctx(player_id=player_id, db=db, records=records, chunithm_net=chunithm_net)
    arcade = await _make_arcade(ctx)

    result = await skill.record_visit(ctx, arcade=arcade, date="2026-10-05")

    assert result.data["settings_saved"] is False
    assert result.data["reason"] == "historical"
    assert chunithm_net.calls == 0
    # 방문 자체는 그래도 남는다.
    visits = await ctx.arcades.visits_for(player_id=player_id, game="chunithm")
    assert len(visits) == 1


async def test_record_visit_network_error_still_records_visit(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    chunithm_net = _FakeChunithmNet(error=ConnectionError("로그인되어 있지 않다"))
    ctx = _ctx(player_id=player_id, db=db, chunithm_net=chunithm_net)
    arcade = await _make_arcade(ctx)

    result = await skill.record_visit(ctx, arcade=arcade)

    assert result.error is None
    assert result.data["settings_saved"] is False
    assert result.data["reason"] == "network_error"
    visits = await ctx.arcades.visits_for(player_id=player_id, game="chunithm")
    assert len(visits) == 1


async def test_record_visit_reports_changed_fields_against_previous(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    chunithm_net = _FakeChunithmNet(settings=_settings(speed="1.00"))
    ctx = _ctx(player_id=player_id, db=db, chunithm_net=chunithm_net)
    arcade = await _make_arcade(ctx)
    await skill.record_visit(ctx, arcade=arcade)

    chunithm_net.settings = _settings(speed="3.00", guide_sound="8")
    result = await skill.record_visit(ctx, arcade=arcade)

    assert sorted(result.data["changed"]) == sorted(["스피드", "가이드음"])


# ---------------------------------------------------------------- get_settings


async def test_get_settings_errors_without_any_visit(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    ctx = _ctx(player_id=player_id, db=db)
    arcade = await _make_arcade(ctx)

    result = await skill.get_settings(ctx, arcade=arcade)

    assert result.error is not None


async def test_get_settings_notes_missing_settings_but_has_visit(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    records = _FakeRecords(sessions={player_id: SessionInfo(id="s1", play_date="2026-10-01", estimated=False)})
    chunithm_net = _FakeChunithmNet(settings=_settings())
    ctx = _ctx(player_id=player_id, db=db, records=records, chunithm_net=chunithm_net)
    arcade = await _make_arcade(ctx)
    # 지난 날짜로 방문만 남긴다(설정은 저장 안 됨).
    await skill.record_visit(ctx, arcade=arcade, date="2026-09-01")

    result = await skill.get_settings(ctx, arcade=arcade)

    assert result.error is None
    assert result.data["settings"] is None


async def test_get_settings_shows_diff_against_most_recently_read_account_settings(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    player_id = await _make_player(db)
    chunithm_net = _FakeChunithmNet(settings=_settings(speed="1.00"))
    ctx = _ctx(player_id=player_id, db=db, chunithm_net=chunithm_net)
    arcade_a = await _make_arcade(ctx, name="겜플")
    arcade_b = await _make_arcade(ctx, name="오락실B")

    await skill.record_visit(ctx, arcade=arcade_a)
    chunithm_net.settings = _settings(speed="4.00")
    await skill.record_visit(ctx, arcade=arcade_b)

    result = await skill.get_settings(ctx, arcade=arcade_a)

    assert result.error is None
    assert "스피드" in result.data["changed"]


# ---------------------------------------------------------------- history


async def test_history_lists_entries_in_date_order(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    records = _FakeRecords()
    chunithm_net = _FakeChunithmNet(settings=_settings(speed="1.00"))
    ctx = _ctx(player_id=player_id, db=db, records=records, chunithm_net=chunithm_net)
    arcade = await _make_arcade(ctx)

    records.sessions[player_id] = SessionInfo(id="s1", play_date="2026-10-01", estimated=False)
    await skill.record_visit(ctx, arcade=arcade)
    chunithm_net.settings = _settings(speed="2.00")
    records.sessions[player_id] = SessionInfo(id="s2", play_date="2026-10-05", estimated=False)
    await skill.record_visit(ctx, arcade=arcade)

    result = await skill.history(ctx, arcade=arcade)

    assert result.error is None
    assert [e["play_date"] for e in result.data["entries"]] == ["2026-10-01", "2026-10-05"]
    table = result.table
    assert table is not None and table.subject == player_id and table.unit == "기록"
    assert [c.label for c in table.columns] == [
        "날짜", "스피드", "필드 월", "판정 타이밍 A", "판정 타이밍 B", "가이드음",
    ]
    first, second = table.rows
    assert first.ref.kind == "chunithm-arcade.settings_entry"
    assert first.ref.key == f'["{arcade.id}", "2026-10-01"]' and first.ref != second.ref
    assert dict(table.summary) == {"오락실": arcade.name}
    assert second.cell("스피드").text == "2.00"
    assert render_text(table).content.splitlines()[-1].startswith("2. 날짜 2026-10-05  스피드 2.00")


async def test_history_errors_when_nothing_stored(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    ctx = _ctx(player_id=player_id, db=db)
    arcade = await _make_arcade(ctx)

    result = await skill.history(ctx, arcade=arcade)

    assert result.error is not None


# ---------------------------------------------------------------- set_privacy


async def test_set_privacy_round_trip(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    ctx = _ctx(player_id=player_id, db=db)

    result = await skill.set_privacy(ctx, public=True)
    assert result.data["public"] is True

    row = await (await db.execute("SELECT public FROM arcade_privacy WHERE player_id = ?", (player_id,))).fetchone()
    assert row is not None
    assert row["public"] == 1


# ---------------------------------------------------------------- on_sync_completed


def _completed_payload(*, has_new_plays: bool = True, arcade_id: str | None = None) -> dict[str, Any]:
    return {"player_id": "p1", "sync_id": "sync1", "has_new_plays": has_new_plays, "arcade_id": arcade_id}


async def test_on_sync_completed_hints_when_no_arcade_given(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(player_id="p1", db=db)

    section = await skill.on_sync_completed(ctx, _completed_payload())

    assert section is not None
    assert section.order == 50
    assert section.display.content == skill._NO_ARCADE_HINT


async def test_on_sync_completed_silent_when_arcade_given(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(player_id="p1", db=db)

    section = await skill.on_sync_completed(ctx, _completed_payload(arcade_id="ar1"))

    assert section is None


async def test_on_sync_completed_silent_without_new_plays(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(player_id="p1", db=db)

    section = await skill.on_sync_completed(ctx, _completed_payload(has_new_plays=False))

    assert section is None


# ---------------------------------------------------------------- on_sync_reported


def _reported_payload(
    *, player_id: str, arcade_id: str | None, has_new_plays: bool = True,
    sessions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "player_id": player_id, "has_new_plays": has_new_plays, "arcade_id": arcade_id,
        "sessions": sessions if sessions is not None else [{"id": "s1", "play_date": "2026-10-11", "estimated": False}],
    }


async def test_on_sync_reported_records_visit_and_settings(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    chunithm_net = _FakeChunithmNet(settings=_settings())
    ctx = _ctx(player_id=player_id, db=db, chunithm_net=chunithm_net)
    arcade = await _make_arcade(ctx)

    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id, arcade_id=arcade.id))

    visits = await ctx.arcades.visits_for(player_id=player_id, game="chunithm")
    assert len(visits) == 1
    assert chunithm_net.calls == 1
    row = await (await db.execute("SELECT * FROM arcade_settings WHERE player_id = ?", (player_id,))).fetchone()
    assert row is not None
    assert row["play_date"] == "2026-10-11"


async def test_on_sync_reported_without_arcade_id_does_nothing(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    ctx = _ctx(player_id=player_id, db=db)

    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id, arcade_id=None))

    visits = await ctx.arcades.visits_for(player_id=player_id, game="chunithm")
    assert visits == []


async def test_on_sync_reported_skips_when_no_new_plays(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    ctx = _ctx(player_id=player_id, db=db)
    arcade = await _make_arcade(ctx)

    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id, arcade_id=arcade.id, has_new_plays=False))

    visits = await ctx.arcades.visits_for(player_id=player_id, game="chunithm")
    assert visits == []


# ---------------------------------------------------------------- on_session_viewed


async def test_on_session_viewed_returns_section_for_self(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    chunithm_net = _FakeChunithmNet(settings=_settings())
    ctx = _ctx(player_id=player_id, db=db, chunithm_net=chunithm_net)
    arcade = await _make_arcade(ctx)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id, arcade_id=arcade.id))

    section = await skill.on_session_viewed(ctx, {"player_id": player_id, "session_id": "s1", "play_date": "2026-10-11"})

    assert section is not None
    assert "스피드" in section.display.content


async def test_on_session_viewed_returns_none_when_nothing_stored(skill: ModuleType, db: aiosqlite.Connection) -> None:
    player_id = await _make_player(db)
    ctx = _ctx(player_id=player_id, db=db)

    section = await skill.on_session_viewed(ctx, {"player_id": player_id, "session_id": "s1", "play_date": "2026-10-11"})

    assert section is None


async def test_on_session_viewed_hides_private_settings_from_others(skill: ModuleType, db: aiosqlite.Connection) -> None:
    owner_id = await _make_player(db)
    viewer_id = await _make_player(db)
    chunithm_net = _FakeChunithmNet(settings=_settings())
    owner_ctx = _ctx(player_id=owner_id, db=db, chunithm_net=chunithm_net)
    arcade = await _make_arcade(owner_ctx)
    await skill.on_sync_reported(owner_ctx, _reported_payload(player_id=owner_id, arcade_id=arcade.id))

    viewer_ctx = _ctx(player_id=viewer_id, db=db)
    section = await skill.on_session_viewed(
        viewer_ctx, {"player_id": owner_id, "session_id": "s1", "play_date": "2026-10-11"}
    )

    assert section is None


async def test_on_session_viewed_shows_settings_to_others_when_public(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    owner_id = await _make_player(db)
    viewer_id = await _make_player(db)
    chunithm_net = _FakeChunithmNet(settings=_settings())
    owner_ctx = _ctx(player_id=owner_id, db=db, chunithm_net=chunithm_net)
    arcade = await _make_arcade(owner_ctx)
    await skill.on_sync_reported(owner_ctx, _reported_payload(player_id=owner_id, arcade_id=arcade.id))
    await skill.set_privacy(owner_ctx, public=True)

    viewer_ctx = _ctx(player_id=viewer_id, db=db)
    section = await skill.on_session_viewed(
        viewer_ctx, {"player_id": owner_id, "session_id": "s1", "play_date": "2026-10-11"}
    )

    assert section is not None
