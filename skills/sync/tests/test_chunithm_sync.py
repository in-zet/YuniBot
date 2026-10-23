# skills/sync/tests/test_chunithm_sync.py
from __future__ import annotations

import dataclasses
import importlib.util
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError

from core.events import EventBus, Section
from core.tables import RowRef, TableBuilder, TableStore, WorkTable, cell
from core.tool_types import Display, ToolContext
from services.common.arcades import Arcade
from services.game.chart_ref import ResolvedChart
from services.game.records import SessionInfo, SyncDiff, SyncResult
from services.game.songdb import ChartRecord, SongRecord

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_sync_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load_skill()
    monkeypatch.setattr(module, "_now", lambda: _NOW)
    return module


# ---------------------------------------------------------------- 가짜 서비스


class _FakeEventBus(EventBus):
    """emit_gather/emit_notify 호출을 기록하고, 모으기에는 미리 정한 조각을 돌려준다."""

    def __init__(self, sections: list[Section] | None = None) -> None:
        super().__init__()
        self._sections = sections or []
        self.gather_calls: list[tuple[str, dict[str, Any]]] = []
        self.notify_calls: list[tuple[str, dict[str, Any]]] = []

    async def emit_gather(
        self, event_name: str, payload: Any, *, player_id: str, guild_id: str | None
    ) -> list[Section]:
        self.gather_calls.append((event_name, payload))
        return list(self._sections)

    async def emit_notify(self, event_name: str, payload: Any, *, player_id: str, guild_id: str | None) -> None:
        self.notify_calls.append((event_name, payload))


class _FakeChunithmNet:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[str] = []

    async def fetch_for_sync(self, player_id: str, *, ctx: ToolContext | None = None) -> object:
        self.calls.append(player_id)
        if self._error is not None:
            raise self._error
        return object()


class _FakeRecords:
    def __init__(
        self, *, result: SyncResult, diff: SyncDiff | None, last_sync_at: str | None
    ) -> None:
        self._result = result
        self._diff = diff
        self._last_sync_at = last_sync_at
        self.save_calls: list[str] = []

    async def last_sync_at(self, player_id: str) -> str | None:
        return self._last_sync_at

    async def save_sync(self, player_id: str, fetch: object, ctx: ToolContext) -> SyncResult:
        self.save_calls.append(player_id)
        # 저장이 끝나면 "마지막 동기화"는 이번 시각으로 바뀐다 — 직전 값을 save 전에 읽는지 확인용.
        self._last_sync_at = _NOW.isoformat()
        return self._result

    async def sync_diff(self, sync_id: str) -> SyncDiff | None:
        return self._diff


class _FakeSettings:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self._values = values or {}
        self.calls: list[dict[str, Any]] = []

    async def get_value(self, *, scope: str, scope_id: str, skill: str, name: str, default: Any) -> Any:
        self.calls.append({"scope": scope, "scope_id": scope_id, "skill": skill, "name": name, "default": default})
        return self._values.get(name, default)


class _FakeYuni:
    def __init__(self) -> None:
        self.calls: list[list[Section]] = []
        self.usage_scopes: list[tuple[str | None, str | None]] = []

    async def line(
        self, sections: list[Section], *, player_id: str | None = None, guild_id: str | None = None
    ) -> str:
        self.calls.append(sections)
        self.usage_scopes.append((player_id, guild_id))
        return "유니 한마디"


class _FakeNotify:
    def __init__(self) -> None:
        self.dms: list[tuple[str, str]] = []

    async def dm(self, player_id: str, content: str, *, view: Any = None) -> None:
        self.dms.append((player_id, content))


@dataclass(slots=True, kw_only=True)
class _P:
    sync_report_public: bool
    primary_discord_id: str | None = "d-primary"


class _FakePlayers:
    def __init__(self, *, public: bool) -> None:
        self._player = _P(sync_report_public=public)

    async def get_player(self, player_id: str) -> _P:
        return self._player


@dataclass(slots=True, kw_only=True)
class _Fakes:
    ctx: ToolContext
    bus: _FakeEventBus
    net: _FakeChunithmNet
    records: _FakeRecords
    settings: _FakeSettings
    yuni: _FakeYuni
    notify: _FakeNotify
    extra: dict[str, Any] = field(default_factory=dict)


def _sync_result(*, first: bool = False, new_plays: bool = True, mismatch: bool = False) -> SyncResult:
    return SyncResult(
        sync_id="sync-1",
        is_first_sync=first,
        has_new_plays=new_plays,
        computed_rating=Decimal("16.94"),
        best30_average=Decimal("17.02"),
        new20_average=Decimal("16.83"),
        displayed_rating=Decimal("16.95") if mismatch else Decimal("16.94"),
        rating_mismatch=mismatch,
        overpower_value=None,
        overpower_percentage=None,
    )


def _diff(sessions: list[SessionInfo]) -> SyncDiff:
    return SyncDiff(
        sync_id="sync-1",
        sessions=sessions,
        chart_updates=[],
        unchanged_plays=[],
        frame_exits=[],
        previous_computed_rating=Decimal("16.90"),
        previous_best30_average=None,
        previous_new20_average=None,
    )


_SESSION = SessionInfo(id="s-1", play_date="2026-10-17", estimated=False)
_REPORT_SECTIONS = [
    Section(order=10, display=Display(content="세션 요약"), data={"kind": "summary"}, stage="good"),
    Section(order=20, display=None, data={"kind": "hint-only"}, hint="오랜만"),
    Section(order=30, display=Display(content="목표 점검"), data={"kind": "goals"}),
]


def ctx_with_fake_services(
    *,
    first_sync: bool = False,
    has_new_plays: bool = True,
    public: bool = True,
    fetch_error: Exception | None = None,
    sections: list[Section] | None = None,
    last_sync_at: str | None = "2026-10-16T12:00:00+00:00",
    settings_values: dict[str, Any] | None = None,
    guild_id: str | None = "g1",
    discord_id: str | None = "d-caller",
    mismatch: bool = False,
) -> _Fakes:
    bus = _FakeEventBus(sections)
    net = _FakeChunithmNet(fetch_error)
    records = _FakeRecords(
        result=_sync_result(first=first_sync, new_plays=has_new_plays, mismatch=mismatch),
        diff=None if first_sync else _diff([_SESSION] if has_new_plays else []),
        last_sync_at=None if first_sync else last_sync_at,
    )
    settings = _FakeSettings(settings_values)
    yuni = _FakeYuni()
    notify = _FakeNotify()
    ctx = ToolContext(
        player_id="p1",
        tags=frozenset({"sega-linked"}),
        services={
            "chunithm_net": net,
            "records": records,
            "settings": settings,
            "yuni": yuni,
            "notify": notify,
            "players": _FakePlayers(public=public),
        },
        guild_id=guild_id,
        event_bus=bus,
        discord_id=discord_id,
    )
    return _Fakes(ctx=ctx, bus=bus, net=net, records=records, settings=settings, yuni=yuni, notify=notify)


def _chart() -> ResolvedChart:
    song = SongRecord(title="Chronomia", aliases=(), artist="테스터", id=1)
    return ResolvedChart(song=song, chart=ChartRecord(difficulty="MAS", const=14.8, note_count=2000))


def _reported(f: _Fakes) -> dict[str, Any]:
    reported = [p for name, p in f.bus.notify_calls if name == "chunithm.sync.reported"]
    assert len(reported) == 1
    return reported[0]


# ---------------------------------------------------------------- 첫 동기화


async def test_run_first_sync_returns_baseline_message_without_report(skill: ModuleType) -> None:
    f = ctx_with_fake_services(first_sync=True)

    result = await skill.run(f.ctx, focus=None, quiet=False, arcade=None)

    assert result.error is None
    assert result.display is not None
    assert "기준점" in (result.display.content or "")
    assert "16.94" in (result.display.content or "")  # 현재 레이팅(코드가 계산한 값)
    assert result.data["computed_rating"] == "16.94"
    assert result.data["is_first_sync"] is True
    assert f.bus.gather_calls == []  # 모으기 이벤트를 안 낸다
    assert f.bus.notify_calls == []  # 리포트 알림도 안 낸다
    assert f.yuni.calls == []
    assert f.notify.dms == []


# ---------------------------------------------------------------- 변화 없음


async def test_run_without_new_plays_still_gathers_and_reports_no_sessions(skill: ModuleType) -> None:
    f = ctx_with_fake_services(has_new_plays=False, sections=[_REPORT_SECTIONS[0]])

    result = await skill.run(f.ctx)

    assert result.error is None
    assert len(f.bus.gather_calls) == 1
    name, payload = f.bus.gather_calls[0]
    assert name == "chunithm.sync.completed"
    assert payload["has_new_plays"] is False
    assert payload["sessions"] == []
    reported = _reported(f)
    # 세션 보관 신호 없음 — 받는 쪽(session)은 has_new_plays=False/빈 sessions로 거른다.
    assert reported["has_new_plays"] is False
    assert reported["sessions"] == []


async def test_completed_payload_has_exact_keys(skill: ModuleType) -> None:
    f = ctx_with_fake_services()

    await skill.run(f.ctx)

    _, payload = f.bus.gather_calls[0]
    assert payload == {
        "player_id": "p1",
        "sync_id": "sync-1",
        "sessions": [{"id": "s-1", "play_date": "2026-10-17", "estimated": False}],
        "has_new_plays": True,
        "is_first_sync": False,
        "focus": None,
        # save_sync 전에 읽은 "직전" 값이다(save 뒤에 읽으면 이번 시각이 된다).
        "previous_synced_at": "2026-10-16T12:00:00+00:00",
        "arcade_id": None,
    }


# ---------------------------------------------------------------- 쿠키 만료


@pytest.mark.parametrize("error", [AuthenticationError("expired"), ConnectionError("로그인되어 있지 않다")])
async def test_run_auth_failure_guides_login_and_skips_save(skill: ModuleType, error: Exception) -> None:
    f = ctx_with_fake_services(fetch_error=error)

    result = await skill.run(f.ctx)

    assert result.error is not None
    assert "/로그인" in result.error
    assert f.records.save_calls == []
    assert f.bus.gather_calls == []
    assert f.bus.notify_calls == []


async def test_run_maintenance_returns_maintenance_error(skill: ModuleType) -> None:
    f = ctx_with_fake_services(fetch_error=MaintenanceError())

    result = await skill.run(f.ctx)

    assert result.error is not None
    assert "점검" in result.error
    assert f.records.save_calls == []


# ---------------------------------------------------------------- 공개 리포트


async def test_run_public_report_returns_full_text_in_display(skill: ModuleType) -> None:
    f = ctx_with_fake_services(public=True, sections=_REPORT_SECTIONS)

    result = await skill.run(f.ctx)

    assert result.error is None
    assert result.display is not None
    assert result.display.content == "유니 한마디\n\n세션 요약\n\n목표 점검"
    assert result.display.ephemeral is False
    assert f.notify.dms == []

    reported = _reported(f)
    assert reported["rendered_text"] == "유니 한마디\n\n세션 요약\n\n목표 점검"
    assert reported["yuni_line"] == "유니 한마디"
    # display가 없는 조각(귀띔만)의 data도 순서대로 들어간다.
    assert reported["sections_data"] == [{"kind": "summary"}, {"kind": "hint-only"}, {"kind": "goals"}]
    assert reported["sent_location"] == {"kind": "channel", "channel_id": None}
    # 모으기 페이로드의 키는 전부 그대로 들어간다.
    _, completed = f.bus.gather_calls[0]
    assert {k: reported[k] for k in completed} == completed
    # 유니 한마디는 요청당 한 번, 모든 조각(표시물 없는 것 포함)을 넘긴다.
    assert len(f.yuni.calls) == 1
    assert f.yuni.calls[0] == _REPORT_SECTIONS
    # 한마디 호출 비용을 이 플레이어·서버에 기록할 수 있게 둘을 넘긴다.
    assert f.yuni.usage_scopes[0][0] == f.ctx.player_id


async def test_run_public_report_without_any_section_still_answers(skill: ModuleType) -> None:
    f = ctx_with_fake_services(public=True, sections=[])

    result = await skill.run(f.ctx, quiet=True)

    assert result.display is not None
    assert (result.display.content or "") != ""
    assert _reported(f)["rendered_text"] == result.display.content


async def test_rating_mismatch_is_noted_at_bottom(skill: ModuleType) -> None:
    f = ctx_with_fake_services(sections=[_REPORT_SECTIONS[0]], mismatch=True)

    result = await skill.run(f.ctx)

    assert result.display is not None
    content = result.display.content or ""
    last_line = content.splitlines()[-1]
    assert "16.94" in last_line and "16.95" in last_line
    assert result.data["rating_mismatch"] is True


# ---------------------------------------------------------------- 비공개 리포트


async def test_run_private_report_sends_dm_and_returns_short_ephemeral(skill: ModuleType) -> None:
    f = ctx_with_fake_services(public=False, sections=_REPORT_SECTIONS)

    result = await skill.run(f.ctx)

    assert f.notify.dms == [("p1", "유니 한마디\n\n세션 요약\n\n목표 점검")]
    assert result.display is not None
    assert result.display.ephemeral is True
    assert "DM" in (result.display.content or "")
    assert "세션 요약" not in (result.display.content or "")
    reported = _reported(f)
    # notify.dm은 대표 계정으로 보낸다 — 실제로 받은 계정을 적는다.
    assert reported["sent_location"] == {"kind": "dm", "discord_id": "d-primary"}
    assert reported["rendered_text"] == "유니 한마디\n\n세션 요약\n\n목표 점검"


# ---------------------------------------------------------------- quiet


async def test_run_quiet_skips_yuni_line(skill: ModuleType) -> None:
    f = ctx_with_fake_services(sections=_REPORT_SECTIONS)

    result = await skill.run(f.ctx, quiet=True)

    assert f.yuni.calls == []
    assert result.display is not None
    assert result.display.content == "세션 요약\n\n목표 점검"
    reported = _reported(f)
    assert reported["yuni_line"] == ""
    assert reported["rendered_text"] == "세션 요약\n\n목표 점검"


# ---------------------------------------------------------------- focus / arcade


async def test_run_focus_and_arcade_go_into_completed_payload(skill: ModuleType) -> None:
    f = ctx_with_fake_services()
    arcade = Arcade(id="a-1", name="동네 오락실", created_by="p1", created_at="2026-10-01")

    await skill.run(f.ctx, focus=_chart(), arcade=arcade)

    _, payload = f.bus.gather_calls[0]
    assert payload["focus"] == [{"song_title": "Chronomia", "difficulty": "MAS"}]
    assert payload["arcade_id"] == "a-1"
    assert _reported(f)["focus"] == [{"song_title": "Chronomia", "difficulty": "MAS"}]


# ---------------------------------------------------------------- 쿨다운


async def test_run_cooldown_blocks_too_fast_retry_with_remaining_seconds(skill: ModuleType) -> None:
    last = (_NOW - timedelta(seconds=30)).isoformat()
    f = ctx_with_fake_services(last_sync_at=last)

    result = await skill.run(f.ctx)

    assert result.error is not None
    assert "90초" in result.error
    assert f.net.calls == []
    assert f.records.save_calls == []
    assert f.settings.calls == [
        {"scope": "guild", "scope_id": "g1", "skill": "sync", "name": "동기화 쿨다운", "default": 120}
    ]


async def test_run_cooldown_follows_setting_value(skill: ModuleType) -> None:
    last = (_NOW - timedelta(seconds=30)).isoformat()
    f = ctx_with_fake_services(last_sync_at=last, settings_values={"동기화 쿨다운": 10})

    result = await skill.run(f.ctx)

    assert result.error is None
    assert f.records.save_calls == ["p1"]


async def test_run_cooldown_rounds_remaining_up(skill: ModuleType) -> None:
    last = (_NOW - timedelta(seconds=119, milliseconds=500)).isoformat()
    f = ctx_with_fake_services(last_sync_at=last)

    result = await skill.run(f.ctx)

    assert result.error is not None
    assert "1초" in result.error


async def test_run_cooldown_without_guild_uses_default(skill: ModuleType) -> None:
    last = (_NOW - timedelta(seconds=30)).isoformat()
    f = ctx_with_fake_services(last_sync_at=last, guild_id=None)

    result = await skill.run(f.ctx)

    assert result.error is not None
    assert "90초" in result.error
    assert f.settings.calls == []


# ---------------------------------------------------------------- 작업 표 옛 값 표시


def _stale_setup(**kwargs: Any) -> tuple[_Fakes, TableStore, ToolContext, str, str, str]:
    f = ctx_with_fake_services(**kwargs)
    store = TableStore()
    ctx = dataclasses.replace(f.ctx, tables=store, channel_id="c1")

    def _table(subject: str | None) -> WorkTable:
        builder = TableBuilder(subject=subject, unit="채보")
        builder.column("곡", bare=True).row(RowRef("chunithm.chart", "a"), {"곡": cell("A")})
        return builder.build()

    mine = store.put("p1", "c1", _table("p1"))
    others = store.put("p1", "c1", _table("p2"))
    plain = store.put("p1", "c1", _table(None))
    return f, store, ctx, mine, others, plain


async def test_sync_marks_only_this_players_tables_stale(skill: ModuleType) -> None:
    _, store, ctx, mine, others, plain = _stale_setup()

    result = await skill.run(ctx)

    assert result.error is None
    assert store.is_stale("p1", "c1", mine)
    assert not store.is_stale("p1", "c1", others)
    assert not store.is_stale("p1", "c1", plain)


async def test_first_sync_also_marks_tables_stale(skill: ModuleType) -> None:
    _, store, ctx, mine, _, _ = _stale_setup(first_sync=True)

    await skill.run(ctx)

    assert store.is_stale("p1", "c1", mine)


async def test_failed_fetch_does_not_mark_tables_stale(skill: ModuleType) -> None:
    _, store, ctx, mine, _, _ = _stale_setup(fetch_error=MaintenanceError())

    result = await skill.run(ctx)

    assert result.error is not None
    assert not store.is_stale("p1", "c1", mine)


async def test_sync_without_table_store_still_works(skill: ModuleType) -> None:
    f = ctx_with_fake_services()

    result = await skill.run(f.ctx)

    assert result.error is None
