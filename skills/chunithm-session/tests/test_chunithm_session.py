# skills/chunithm-session/tests/test_chunithm_session.py
from __future__ import annotations

import importlib.util
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
from core.threads import ThreadMessage, ThreadTranscript
from core.tool_types import ToolContext
from services.chunithm.records import PbRow, SyncLogSummary
from services.common.arcades import Arcade, ArcadeVisit
from services.common.players import Player, PlayersService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_CORE_PLAYERS_SQL = Path("migrations/core/001_players.sql").read_text()
_MIGRATIONS = sorted((Path(__file__).parent.parent / "migrations").glob("*.sql"))


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_session_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load_skill()
    monkeypatch.setattr(module, "_ASK_DELAY_SECONDS", 0.0)
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


@dataclass(slots=True, kw_only=True)
class _FakeSettings:
    values: dict[str, Any] = field(default_factory=dict)

    async def get_value(self, *, scope: str, scope_id: str, skill: str, name: str, default: Any) -> Any:
        return self.values.get(name, default)


@dataclass(slots=True, kw_only=True)
class _FakeYuni:
    asked: list[dict[str, Any]] = field(default_factory=list)

    async def line(self, sections: list[Any], *, player_id: str | None = None, guild_id: str | None = None,
                   fallback: str | None = None) -> str:
        self.asked.append({"sections": sections, "fallback": fallback})
        return "이번엔 어땠어?"


@dataclass(slots=True, kw_only=True)
class _FakeNotify:
    opened: list[dict[str, Any]] = field(default_factory=list)
    open_threads: set[tuple[str, str]] = field(default_factory=set)
    can_open: bool = True
    raise_on_open: bool = False

    def has_open_thread(self, player_id: str, skill_name: str) -> bool:
        return (player_id, skill_name) in self.open_threads

    async def open_dm_thread(self, **kwargs: Any) -> object | None:
        if self.raise_on_open:
            raise RuntimeError("DM 차단")
        if not self.can_open:
            return None
        self.opened.append(kwargs)
        self.open_threads.add((kwargs["player_id"], kwargs["skill_name"]))
        return object()


def _ctx(
    *, player_id: str, db: aiosqlite.Connection,
    records: _FakeRecords | None = None, arcades: _FakeArcades | None = None,
    players: PlayersService | None = None, notify: _FakeNotify | None = None,
    settings: _FakeSettings | None = None, yuni: _FakeYuni | None = None, bus: EventBus | None = None,
    guild_id: str | None = None,
) -> ToolContext:
    services: dict[str, object] = {
        "records": records or _FakeRecords(), "arcades": arcades or _FakeArcades(),
        "players": players or PlayersService(db), "notify": notify or _FakeNotify(),
        "settings": settings or _FakeSettings(), "yuni": yuni or _FakeYuni(),
    }
    return ToolContext(
        player_id=player_id, tags=frozenset(), services=services, db=db, event_bus=bus or EventBus(), guild_id=guild_id
    )


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


# ---------------------------------------------------------------- 후기


class _RecordingBus(EventBus):
    def __init__(self) -> None:
        super().__init__()
        self.emitted: list[tuple[str, Any]] = []

    async def emit_notify(self, event_name: str, payload: Any, *, player_id: str, guild_id: str | None) -> None:
        self.emitted.append((event_name, payload))


_T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _transcript(
    *, player_id: str, lines: list[tuple[str, bool]], reason: str = "explicit"
) -> ThreadTranscript:
    """lines: (내용, 유니가 한 말인지)"""
    return ThreadTranscript(
        thread_id="t1", player_id=player_id, closed_reason=reason, skill_name="chunithm-session", channel_id="dm-1",  # pyright: ignore[reportArgumentType]
        messages=[
            ThreadMessage(player_id=player_id, content=text, at=_T0 + timedelta(seconds=i), is_yuni=is_yuni)
            for i, (text, is_yuni) in enumerate(lines)
        ],
    )


async def _reported(skill: ModuleType, db: aiosqlite.Connection, **ctx_kwargs: Any) -> tuple[ToolContext, _FakeNotify, str]:
    players = PlayersService(db)
    me = await players.create_player()
    notify = ctx_kwargs.pop("notify", None) or _FakeNotify()
    ctx = _ctx(db=db, player_id=me.id, players=players, notify=notify, **ctx_kwargs)
    return ctx, notify, me.id


async def test_report_with_new_plays_asks_for_a_review_in_dm(skill: ModuleType, db: aiosqlite.Connection) -> None:
    yuni = _FakeYuni()
    ctx, notify, player_id = await _reported(skill, db, yuni=yuni)

    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))

    assert len(notify.opened) == 1
    opened = notify.opened[0]
    assert opened["skill_name"] == "chunithm-session"
    assert opened["opening"] == "이번엔 어땠어?"
    assert opened["idle_timeout"] == 300.0 and opened["close_timeout"] == 300.0
    assert "세션 요약 2026-10-05" in opened["guidance"]
    assert "end_conversation" in opened["guidance"]
    assert yuni.asked[0]["fallback"] == "이번엔 어땠어?"
    # 질문에 쓰는 힌트에는 숫자가 없어서 유니가 날짜를 읊다가 고정 문구로 되돌아가지 않는다.
    assert not any(ch.isdigit() for ch in yuni.asked[0]["sections"][0].hint)


async def test_report_without_new_plays_asks_nothing(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, notify, player_id = await _reported(skill, db)

    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id, has_new_plays=False))

    assert notify.opened == []


async def test_review_question_respects_the_player_setting(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, notify, player_id = await _reported(skill, db, settings=_FakeSettings(values={"후기 물어보기": False}))

    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))

    assert notify.opened == []


async def test_review_question_skipped_while_a_review_is_already_open(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    notify = _FakeNotify()
    ctx, notify, player_id = await _reported(skill, db, notify=notify)
    notify.open_threads.add((player_id, "chunithm-session"))

    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))

    assert notify.opened == []


async def test_review_asks_only_about_the_latest_session(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, notify, player_id = await _reported(skill, db)
    payload = _reported_payload(player_id=player_id, session_id="s1", play_date="2026-10-01")
    payload["sessions"] = [
        {"id": "s1", "play_date": "2026-10-01", "estimated": False},
        {"id": "s2", "play_date": "2026-10-03", "estimated": False},
    ]

    await skill.on_sync_reported(ctx, payload)

    assert len(notify.opened) == 1
    assert "2026-10-03" in notify.opened[0]["guidance"]


async def test_review_uses_the_guild_timeout_settings(skill: ModuleType, db: aiosqlite.Connection) -> None:
    settings = _FakeSettings(values={"후기 무응답 확인 시간": 2, "후기 종결 시간": 7})
    ctx, notify, player_id = await _reported(skill, db, settings=settings, guild_id="g1")

    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))

    assert notify.opened[0]["idle_timeout"] == 120.0
    assert notify.opened[0]["close_timeout"] == 420.0


async def test_report_is_kept_even_when_the_dm_cannot_be_opened(skill: ModuleType, db: aiosqlite.Connection) -> None:
    for notify in (_FakeNotify(can_open=False), _FakeNotify(raise_on_open=True)):
        ctx, _notify, player_id = await _reported(skill, db, notify=notify)
        await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id, session_id=f"s-{player_id}"))
        rows = await (await db.execute("SELECT COUNT(*) AS n FROM session_reports WHERE player_id = ?", (player_id,))).fetchone()
        assert rows is not None and rows["n"] == 1


async def _close(skill: ModuleType, notify: _FakeNotify, transcript: ThreadTranscript) -> None:
    await notify.opened[-1]["on_close"](transcript)


async def test_closing_saves_the_full_transcript_and_emits_closed(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    bus = _RecordingBus()
    ctx, notify, player_id = await _reported(skill, db, bus=bus)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))

    await _close(skill, notify, _transcript(
        player_id=player_id, lines=[("이번엔 어땠어?", True), ("크로노 처음으로 SSS 떴어", False), ("우와 축하해!", True)]
    ))

    row = await (await db.execute("SELECT * FROM session_reviews")).fetchone()
    assert row is not None
    assert row["status"] == "완료" and row["is_public"] == 0 and row["player_id"] == player_id
    saved = json.loads(row["messages"])
    assert [(m["who"], m["text"]) for m in saved] == [
        ("yuni", "이번엔 어땠어?"), ("player", "크로노 처음으로 SSS 떴어"), ("yuni", "우와 축하해!"),
    ]
    assert bus.emitted[-1][0] == "chunithm.session.closed"
    payload = bus.emitted[-1][1]
    assert payload["saved"] is True and payload["status"] == "완료" and payload["session_id"] == "s1"
    assert "나: 크로노 처음으로 SSS 떴어" in payload["review"]


async def test_closing_without_the_player_speaking_marks_it_skipped(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    bus = _RecordingBus()
    ctx, notify, player_id = await _reported(skill, db, bus=bus)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))

    await _close(skill, notify, _transcript(player_id=player_id, lines=[("이번엔 어땠어?", True)], reason="timeout"))

    row = await (await db.execute("SELECT status FROM session_reviews")).fetchone()
    assert row is not None and row["status"] == "넘김"
    assert bus.emitted[-1][1]["saved"] is False


async def test_shutdown_during_a_conversation_marks_it_aborted_and_it_is_asked_again(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    ctx, notify, player_id = await _reported(skill, db)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))
    await _close(skill, notify, _transcript(
        player_id=player_id, lines=[("이번엔 어땠어?", True), ("음 그게", False)], reason="shutdown"
    ))
    row = await (await db.execute("SELECT status FROM session_reviews")).fetchone()
    assert row is not None and row["status"] == "중단"

    notify.open_threads.clear()
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))
    assert len(notify.opened) == 2  # 중단된 후기는 다음 동기화에서 다시 묻는다


async def test_finished_or_skipped_reviews_are_not_asked_again(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, notify, player_id = await _reported(skill, db)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))
    await _close(skill, notify, _transcript(player_id=player_id, lines=[("어땠어?", True)], reason="timeout"))
    notify.open_threads.clear()

    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))

    assert len(notify.opened) == 1


async def test_a_later_review_is_appended_after_the_earlier_one(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, notify, player_id = await _reported(skill, db)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))
    await _close(skill, notify, _transcript(player_id=player_id, lines=[("어땠어?", True), ("좋았어", False)]))
    first = await (await db.execute("SELECT started_at FROM session_reviews")).fetchone()
    assert first is not None
    notify.open_threads.clear()

    result = await skill.review_open(ctx)
    assert result.error is None
    await _close(skill, notify, _transcript(player_id=player_id, lines=[("더 있어?", True), ("하나 더", False)]))

    row = await (await db.execute("SELECT * FROM session_reviews")).fetchone()
    assert row is not None
    assert [m["text"] for m in json.loads(row["messages"])] == ["어땠어?", "좋았어", "더 있어?", "하나 더"]
    assert row["started_at"] == first["started_at"]


async def test_skipping_a_later_review_keeps_the_earlier_one(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, notify, player_id = await _reported(skill, db)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))
    await _close(skill, notify, _transcript(player_id=player_id, lines=[("어땠어?", True), ("좋았어", False)]))
    notify.open_threads.clear()
    _ = await skill.review_open(ctx)

    await _close(skill, notify, _transcript(player_id=player_id, lines=[("더 있어?", True)], reason="timeout"))

    row = await (await db.execute("SELECT status, messages FROM session_reviews")).fetchone()
    assert row is not None and row["status"] == "완료"
    assert [m["text"] for m in json.loads(row["messages"])] == ["어땠어?", "좋았어"]


async def test_a_failing_save_does_not_break_closing(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, notify, player_id = await _reported(skill, db)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))
    await db.execute("DROP TABLE session_reviews")

    await _close(skill, notify, _transcript(player_id=player_id, lines=[("어땠어?", True), ("좋았어", False)]))


# ---------------------------------------------------------------- review_open


async def test_review_open_defaults_to_the_latest_session(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, notify, player_id = await _reported(skill, db, settings=_FakeSettings(values={"후기 물어보기": False}))
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id, session_id="s1", play_date="2026-10-01"))
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id, session_id="s2", play_date="2026-10-05"))

    result = await skill.review_open(ctx)

    assert result.error is None
    assert result.data["play_date"] == "2026-10-05"
    assert "2026-10-05" in notify.opened[0]["guidance"]


async def test_review_open_by_date_works_even_when_asking_is_turned_off(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    ctx, notify, player_id = await _reported(skill, db, settings=_FakeSettings(values={"후기 물어보기": False}))
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id, session_id="s1", play_date="2026-10-01"))
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id, session_id="s2", play_date="2026-10-05"))

    result = await skill.review_open(ctx, date="2026-10-01")

    assert result.error is None
    assert "2026-10-01" in notify.opened[0]["guidance"]


async def test_review_open_errors(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, notify, player_id = await _reported(skill, db, settings=_FakeSettings(values={"후기 물어보기": False}))
    assert (await skill.review_open(ctx)).error == skill._NOT_SYNCED_ERROR

    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id, session_id="s1", play_date="2026-10-01"))
    missing = await skill.review_open(ctx, date="2026-10-09")
    assert missing.error is not None and missing.data["nearby"] == {"before": "2026-10-01", "after": None}

    _ = await skill.review_open(ctx)
    assert (await skill.review_open(ctx)).error is not None  # 이미 열려 있다

    notify.open_threads.clear()
    notify.can_open = False
    assert (await skill.review_open(ctx)).error is not None  # DM을 열 수 없다


# ---------------------------------------------------------------- 후기 보기와 삭제


async def _with_review(skill: ModuleType, db: aiosqlite.Connection) -> tuple[ToolContext, str]:
    ctx, notify, player_id = await _reported(skill, db)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))
    await _close(skill, notify, _transcript(
        player_id=player_id, lines=[("어땠어?", True), ("크로노 SSS 떴어", False)]
    ))
    return ctx, player_id


async def test_get_session_shows_the_review_to_its_owner_only(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, player_id = await _with_review(skill, db)
    own = await skill.get_session(ctx)
    assert own.data["review"]["status"] == "완료"
    assert own.display is not None and own.display.content is not None
    assert "나: 크로노 SSS 떴어" in own.display.content

    players = PlayersService(db)
    await players.set_visibility(player_id, "records_public", True)
    viewer = await players.create_player()
    owner = await players.get_player(player_id)
    seen = await skill.get_session(_ctx(db=db, player_id=viewer.id, players=players), player=owner)
    assert seen.error is None
    assert "review" not in seen.data
    assert seen.display is not None and seen.display.content is not None
    assert "크로노 SSS 떴어" not in seen.display.content


async def test_get_session_cuts_a_long_review(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, notify, player_id = await _reported(skill, db)
    await skill.on_sync_reported(ctx, _reported_payload(player_id=player_id))
    await _close(skill, notify, _transcript(player_id=player_id, lines=[("가" * 900, False)]))

    result = await skill.get_session(ctx)

    assert result.display is not None and result.display.content is not None
    assert "가" * 900 not in result.display.content
    assert len(result.data["review"]["text"]) > 900  # data에는 전문이 있다


async def test_deleting_a_session_deletes_its_review(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx, _player_id = await _with_review(skill, db)

    result = await skill.delete_session(ctx, date="2026-10-05")

    assert result.error is None
    row = await (await db.execute("SELECT COUNT(*) AS n FROM session_reviews")).fetchone()
    assert row is not None and row["n"] == 0
