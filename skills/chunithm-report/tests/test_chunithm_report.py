# skills/chunithm-report/tests/test_chunithm_report.py
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from chuni_penguin.adapters.errors import AuthenticationError
from chuni_penguin.types import Chart, Difficulty, Song
from chuni_penguin.types.lamp import ClearLamp, ComboLamp
from chuni_penguin.types.score import Judgements, RecentScore

from core.events import Section
from core.tool_types import Display, ToolContext, ToolResult
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.records import (
    CandidateHit,
    ChartUpdate,
    FrameExit,
    PbRow,
    RatingDeltas,
    SessionInfo,
    SyncDiff,
    SyncLogSummary,
    UnchangedPlay,
)
from services.chunithm.songdb import ChartRecord, SongRecord
from services.common.players import Player

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_JST = ZoneInfo("Asia/Tokyo")


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_report_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


# ---------------------------------------------------------------- 가짜 서비스


def _player(player_id: str, *, public: bool = True) -> Player:
    return Player(
        id=player_id, primary_discord_id=None, records_public=public, memos_public=False,
        goals_public=False, arcade_public=False, reviews_public=False, sync_report_public=True,
        first_seen_at="2026-10-01T00:00:00+00:00",
    )


class _FakePlayers:
    def __init__(self, players: list[Player]) -> None:
        self._players = {p.id: p for p in players}

    async def get_player(self, player_id: str) -> Player | None:
        return self._players.get(player_id)


def _pb(title: str, difficulty: str, score: int, rank: str, const: float | None, rating: str | None) -> PbRow:
    return PbRow(
        song_title=title, difficulty=difficulty, score=score, rank=rank, clear_lamp="CLEAR",
        combo_lamp="FULL COMBO", chain_lamp="NONE", const=const,
        rating=Decimal(rating) if rating is not None else None, session_id=None,
        updated_at="2026-10-16T12:00:00+00:00",
    )


_DELTAS_FIRST = RatingDeltas(
    computed_rating=Decimal("16.94"), best30_average=Decimal("17.02"), new20_average=Decimal("16.83"),
    has_previous=False, previous_computed_rating=None, previous_best30_average=None,
    previous_new20_average=None,
)
_DELTAS_SECOND = RatingDeltas(
    computed_rating=Decimal("16.94"), best30_average=Decimal("17.02"), new20_average=Decimal("16.83"),
    has_previous=True, previous_computed_rating=Decimal("16.90"), previous_best30_average=Decimal("17.00"),
    previous_new20_average=Decimal("16.83"),
)

_SUMMARY = SyncLogSummary(
    nickname="펭귄", computed_rating=Decimal("16.94"), best30_average=Decimal("17.02"),
    new20_average=Decimal("16.83"), last_synced_at="2026-10-17T03:00:00+00:00",
)


@dataclass(slots=True, kw_only=True)
class _FakeRecords:
    summaries: dict[str, SyncLogSummary] = field(default_factory=dict)
    deltas: dict[str, RatingDeltas] = field(default_factory=dict)
    pbs: dict[str, list[PbRow]] = field(default_factory=dict)
    frames: dict[str, tuple[list[PbRow], list[PbRow]]] = field(default_factory=dict)
    hits: list[CandidateHit] = field(default_factory=list)
    diffs: dict[str, SyncDiff] = field(default_factory=dict)
    candidate_calls: list[dict[str, Any]] = field(default_factory=list)

    async def latest_sync_summary(self, player_id: str) -> SyncLogSummary | None:
        return self.summaries.get(player_id)

    async def rating_deltas(self, player_id: str) -> RatingDeltas | None:
        return self.deltas.get(player_id)

    async def pb_row(self, player_id: str, *, song_title: str, difficulty: str) -> PbRow | None:
        for row in self.pbs.get(player_id, []):
            if (row.song_title, row.difficulty) == (song_title, difficulty):
                return row
        return None

    async def all_pb_rows(self, player_id: str) -> list[PbRow]:
        return list(self.pbs.get(player_id, []))

    async def current_frames(self, player_id: str) -> tuple[list[PbRow], list[PbRow]]:
        return self.frames.get(player_id, ([], []))

    async def candidates(
        self, player_id: str, *, min_const: float | None, max_const: float | None, count: int
    ) -> list[CandidateHit]:
        self.candidate_calls.append(
            {"player_id": player_id, "min_const": min_const, "max_const": max_const, "count": count}
        )
        return self.hits[:count]

    async def sync_diff(self, sync_id: str) -> SyncDiff | None:
        return self.diffs.get(sync_id)


class _FakeSongDb:
    def __init__(self, consts: dict[tuple[str, str], float] | None = None) -> None:
        self._consts = consts or {}

    def const_for(self, title: str, difficulty: str) -> float | None:
        return self._consts.get((title, difficulty))


def _recent(title: str, score: int, *, detailed: bool = False) -> RecentScore:
    return RecentScore(
        song=Song(id=0, title=title),
        chart=Chart(difficulty=Difficulty("MAS")),
        score=score,
        clear_lamp=ClearLamp.clear,
        combo_lamp=ComboLamp.none,
        achieved_at=datetime(2026, 10, 16, 20, 0, tzinfo=_JST),
        judgements=(
            Judgements(justice_critical=1990, justice=8, attack=2, miss=0) if detailed else None
        ),
    )


class _FakeChunithmNet:
    def __init__(self, scores: list[RecentScore], *, error: Exception | None = None) -> None:
        self._scores = scores
        self._error = error
        self.enrich_calls: list[tuple[str, int, int]] = []  # (player_id, 넘긴 개수, limit)

    async def get_recent_scores(self, player_id: str) -> list[RecentScore]:
        if self._error is not None:
            raise self._error
        return list(self._scores)

    async def enrich_recent_with_detail(
        self, player_id: str, scores: list[RecentScore], *, limit: int
    ) -> list[RecentScore]:
        self.enrich_calls.append((player_id, len(scores), limit))
        detailed = [_recent(s.song.title, s.score, detailed=True) for s in scores[:limit]]
        return detailed + scores[limit:]


@dataclass(slots=True, kw_only=True)
class _Fakes:
    ctx: ToolContext
    records: _FakeRecords
    net: _FakeChunithmNet


def _ctx(
    *,
    records: _FakeRecords | None = None,
    players: list[Player] | None = None,
    net: _FakeChunithmNet | None = None,
    consts: dict[tuple[str, str], float] | None = None,
) -> _Fakes:
    records = records or _FakeRecords()
    net = net or _FakeChunithmNet([])
    ctx = ToolContext(
        player_id="me",
        tags=frozenset({"chunithm-synced"}),
        services={
            "records": records,
            "songdb": _FakeSongDb(consts),
            "players": _FakePlayers(players or [_player("me", public=False)]),
            "chunithm_net": net,
        },
    )
    return _Fakes(ctx=ctx, records=records, net=net)


def _content(result: ToolResult) -> str:
    assert result.display is not None
    return result.display.content or ""


def _chart(title: str = "Chronomia", difficulty: str = "MAS") -> ResolvedChart:
    song = SongRecord(title=title, aliases=(), artist="테스터")
    return ResolvedChart(song=song, chart=ChartRecord(difficulty=difficulty, const=14.8, note_count=2000))


# ---------------------------------------------------------------- profile (Step 2)


async def test_profile_self_ignores_privacy_setting(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}), players=[_player("me", public=False)])

    result = await skill.profile(f.ctx)

    assert result.error is None
    assert result.display_mode == "optional"
    assert result.data["computed_rating"] == "16.94"
    assert result.data["best30_average"] == "17.02"
    assert result.data["new20_average"] == "16.83"
    assert result.data["nickname"] == "펭귄"
    content = _content(result)
    assert "16.94" in content and "17.02" in content and "16.83" in content and "펭귄" in content


async def test_profile_self_resolved_explicitly_also_ignores_privacy(skill: ModuleType) -> None:
    me = _player("me", public=False)
    f = _ctx(records=_FakeRecords(summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}), players=[me])

    result = await skill.profile(f.ctx, player=me)

    assert result.error is None


async def test_profile_of_private_other_player_is_refused(skill: ModuleType) -> None:
    other = _player("other", public=False)
    f = _ctx(records=_FakeRecords(summaries={"other": _SUMMARY}, deltas={"other": _DELTAS_SECOND}), players=[_player("me"), other])

    result = await skill.profile(f.ctx, player=other)

    assert result.error is not None and "비공개" in result.error
    assert result.data == {}


async def test_profile_of_public_other_player(skill: ModuleType) -> None:
    other = _player("other", public=True)
    f = _ctx(records=_FakeRecords(summaries={"other": _SUMMARY}, deltas={"other": _DELTAS_SECOND}), players=[_player("me"), other])

    result = await skill.profile(f.ctx, player=other)

    assert result.error is None
    assert result.data["player_id"] == "other"


async def test_profile_shows_change_since_previous_sync(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    result = await skill.profile(f.ctx)

    assert result.data["change"] == {"computed_rating": "+0.04", "best30_average": "+0.02", "new20_average": "+0.00"}
    assert result.data["previous"]["computed_rating"] == "16.90"
    content = _content(result)
    assert "16.94 (+0.04)" in content and "17.02 (+0.02)" in content and "16.83 (+0.00)" in content


async def test_profile_after_first_sync_has_no_previous(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_FIRST}))

    result = await skill.profile(f.ctx)

    assert result.data["has_previous"] is False
    assert result.data["change"] == {"computed_rating": None, "best30_average": None, "new20_average": None}
    assert "16.94 (비교할 직전 기록 없음)" in _content(result)


async def test_profile_never_synced(skill: ModuleType) -> None:
    f = _ctx()

    result = await skill.profile(f.ctx)

    assert result.error is not None and "동기화" in result.error


# ---------------------------------------------------------------- best (Step 3)


def _frames() -> tuple[list[PbRow], list[PbRow]]:
    best = [_pb("곡 A", "MAS", 1_007_620, "SSS", 15.3, "17.31"), _pb("곡 B", "MAS", 1_008_700, "SSS", 14.9, "17.02")]
    new = [_pb("신곡 C", "EXP", 1_009_000, "SSS+", 14.0, "16.15")]
    return best, new


async def test_best_never_synced(skill: ModuleType) -> None:
    f = _ctx()

    result = await skill.best(f.ctx)

    assert result.error is not None and "동기화" in result.error


async def test_best_both_lists_rows_and_contribution(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(frames={"me": _frames()}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    result = await skill.best(f.ctx)

    assert result.error is None
    assert result.display_mode == "required"
    assert [r["song_title"] for r in result.data["best"]] == ["곡 A", "곡 B"]
    assert [r["song_title"] for r in result.data["new"]] == ["신곡 C"]
    # 전체 레이팅 기여도 = 곡 레이팅 / 50 (정확한 값, 반올림 없음)
    assert result.data["best"][0]["contribution"] == "0.3462"
    content = _content(result)
    assert "곡 A" in content and "신곡 C" in content and "1,007,620" in content and "0.3462" in content


async def test_best_shows_average_changes(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(frames={"me": _frames()}, deltas={"me": _DELTAS_SECOND}))

    result = await skill.best(f.ctx)

    assert result.data["best_average"] == {"current": "17.02", "previous": "17.00", "change": "+0.02"}
    assert result.data["computed_rating"]["change"] == "+0.04"
    content = _content(result)
    assert "레이팅  16.94 (+0.04)" in content
    assert "베스트 30  평균 17.02 (+0.02)" in content
    assert "신곡 20  평균 16.83 (+0.00)" in content


async def test_best_after_first_sync_has_no_previous(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(frames={"me": _frames()}, deltas={"me": _DELTAS_FIRST}))

    result = await skill.best(f.ctx)

    assert result.data["best_average"]["change"] is None
    assert "베스트 30  평균 17.02 (비교할 직전 기록 없음)" in _content(result)


async def test_best_kind_new_only(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(frames={"me": _frames()}, deltas={"me": _DELTAS_SECOND}))

    result = await skill.best(f.ctx, kind="new")

    assert "best" not in result.data
    assert [r["song_title"] for r in result.data["new"]] == ["신곡 C"]
    assert "곡 A" not in _content(result)


async def test_best_invalid_kind_is_error(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(frames={"me": _frames()}, deltas={"me": _DELTAS_SECOND}))

    result = await skill.best(f.ctx, kind="전부")

    assert result.error is not None


async def test_best_of_private_other_is_refused(skill: ModuleType) -> None:
    other = _player("other", public=False)
    f = _ctx(records=_FakeRecords(frames={"other": _frames()}, deltas={"other": _DELTAS_SECOND}), players=[_player("me"), other])

    result = await skill.best(f.ctx, player=other)

    assert result.error is not None and "비공개" in result.error


# ---------------------------------------------------------------- recent (Step 3)


async def test_recent_enriches_only_first_twenty_with_judgements(skill: ModuleType) -> None:
    scores = [_recent(f"곡{i}", 1_000_000 + i) for i in range(25)]
    f = _ctx(net=_FakeChunithmNet(scores))

    result = await skill.recent(f.ctx, count=25)

    assert result.error is None
    assert result.display_mode == "required"
    assert f.net.enrich_calls == [("me", 25, 20)]
    plays = result.data["plays"]
    assert len(plays) == 25
    assert all(p["judgements"] is not None for p in plays[:20])
    assert all(p["judgements"] is None for p in plays[20:])
    assert plays[0]["judgements"] == {"justice_critical": 1990, "justice": 8, "attack": 2, "miss": 0}
    assert "20" in _content(result)  # 판정 수가 앞 20개에만 있다는 안내


async def test_recent_count_limits_shown_plays(skill: ModuleType) -> None:
    scores = [_recent(f"곡{i}", 1_000_000 + i) for i in range(30)]
    f = _ctx(net=_FakeChunithmNet(scores))

    result = await skill.recent(f.ctx, count=5)

    assert len(result.data["plays"]) == 5
    # 보여 줄 5개만 넘긴다 — 상세 판정 요청(네트워크)을 쓸데없이 20번 하지 않는다.
    assert f.net.enrich_calls == [("me", 5, 20)]


async def test_recent_login_expired(skill: ModuleType) -> None:
    f = _ctx(net=_FakeChunithmNet([], error=AuthenticationError("expired")))

    result = await skill.recent(f.ctx, count=5)

    assert result.error is not None and "/로그인" in result.error


async def test_recent_of_private_other_is_refused_before_network(skill: ModuleType) -> None:
    other = _player("other", public=False)
    f = _ctx(net=_FakeChunithmNet([_recent("곡", 1_000_000)]), players=[_player("me"), other])

    result = await skill.recent(f.ctx, player=other, count=5)

    assert result.error is not None and "비공개" in result.error
    assert f.net.enrich_calls == []


# ---------------------------------------------------------------- record (Step 3)


async def test_record_not_played(skill: ModuleType) -> None:
    f = _ctx()

    result = await skill.record(f.ctx, chart=_chart())

    assert result.error is not None and "기록" in result.error


async def test_record_found(skill: ModuleType) -> None:
    row = _pb("Chronomia", "MAS", 1_007_620, "SSS", 14.8, "16.80")
    f = _ctx(records=_FakeRecords(pbs={"me": [row]}))

    result = await skill.record(f.ctx, chart=_chart())

    assert result.error is None
    assert result.display_mode == "optional"
    assert result.data["score"] == 1_007_620
    assert result.data["rank"] == "SSS"
    assert result.data["rating"] == "16.80"
    assert "1,007,620" in _content(result)


async def test_record_of_private_other_is_refused(skill: ModuleType) -> None:
    other = _player("other", public=False)
    row = _pb("Chronomia", "MAS", 1_007_620, "SSS", 14.8, "16.80")
    f = _ctx(records=_FakeRecords(pbs={"other": [row]}), players=[_player("me"), other])

    result = await skill.record(f.ctx, chart=_chart(), player=other)

    assert result.error is not None and "비공개" in result.error


# ---------------------------------------------------------------- analyze (Step 3)


def _analyze_rows() -> list[PbRow]:
    return [
        _pb("a", "MAS", 1_007_000, "SS+", 15.3, "17.00"),
        _pb("b", "MAS", 1_009_000, "SSS+", 15.9, "18.05"),
        _pb("c", "MAS", 1_000_000, "SS", 14.2, "15.20"),
        _pb("d", "EXP", 990_001, "S+", 14.0, "14.60"),
        _pb("e", "MAS", 1_002_000, "SS", None, None),
    ]


async def test_analyze_never_synced(skill: ModuleType) -> None:
    f = _ctx()

    result = await skill.analyze(f.ctx)

    assert result.error is not None and "동기화" in result.error


async def test_analyze_groups_by_integer_const_band(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(pbs={"me": _analyze_rows()}))

    result = await skill.analyze(f.ctx)

    assert result.error is None
    assert result.display_mode == "required"
    bands = {b["band"]: b for b in result.data["bands"]}
    assert list(bands) == [15, 14]  # 높은 상수대부터
    assert bands[15]["count"] == 2
    assert bands[15]["average_score"] == 1_008_000
    assert bands[15]["ranks"] == {"SSS+": 1, "SS+": 1}
    assert bands[14]["average_score"] == 995_000  # (1,000,000 + 990,001) // 2
    assert bands[14]["ranks"] == {"SS": 1, "S+": 1}
    assert result.data["unknown_const_count"] == 1
    assert "15대" in _content(result)
    assert "attack" not in str(result.data).lower()  # 결정 사항 E — 어택·미스 비중은 없다


async def test_analyze_const_range_filter(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(pbs={"me": _analyze_rows()}))

    result = await skill.analyze(f.ctx, min_const=15.0, max_const=15.5)

    bands = result.data["bands"]
    assert [b["band"] for b in bands] == [15]
    assert bands[0]["count"] == 1
    assert bands[0]["average_score"] == 1_007_000


async def test_analyze_of_private_other_is_refused(skill: ModuleType) -> None:
    other = _player("other", public=False)
    f = _ctx(records=_FakeRecords(pbs={"other": _analyze_rows()}), players=[_player("me"), other])

    result = await skill.analyze(f.ctx, player=other)

    assert result.error is not None and "비공개" in result.error


# ---------------------------------------------------------------- candidates (Step 3)


def _hit(title: str, upside: str, *, remaining: int | None = 7_500) -> CandidateHit:
    return CandidateHit(
        song_title=title, difficulty="MAS", const=15.0, upside=Decimal(upside), current_score=1_000_000,
        remaining_score=remaining, remaining_attacks=Decimal("75.00") if remaining is not None else None,
    )


async def test_candidates_passes_range_and_count_for_self(skill: ModuleType) -> None:
    hits = [_hit("곡 A", "0.50"), _hit("곡 B", "0.30", remaining=None)]
    f = _ctx(records=_FakeRecords(hits=hits))

    result = await skill.candidates(f.ctx, min_const=14.5, max_const=15.5, count=5)

    assert f.records.candidate_calls == [{"player_id": "me", "min_const": 14.5, "max_const": 15.5, "count": 5}]
    assert result.display_mode == "required"
    assert [h["song_title"] for h in result.data["candidates"]] == ["곡 A", "곡 B"]
    assert result.data["candidates"][0]["upside"] == "0.50"
    assert result.data["candidates"][1]["remaining_score"] is None
    content = _content(result)
    assert "곡 A" in content and "7,500" in content and "계산 불가" in content


async def test_candidates_empty(skill: ModuleType) -> None:
    f = _ctx()

    result = await skill.candidates(f.ctx)

    assert result.error is None
    assert result.data["candidates"] == []
    assert "없" in _content(result)


# ---------------------------------------------------------------- 동기화 리포트 조각 (Step 4, 5)

_S1 = SessionInfo(id="s1", play_date="2026-10-15", estimated=False)
_S2 = SessionInfo(id="s2", play_date="2026-10-16", estimated=True)


def _update(
    title: str, *, old: int | None, new: int, old_rank: str | None, new_rank: str,
    old_rating: str | None, new_rating: str, session: str = "s1", estimated: bool = False,
    entered: bool = False,
) -> ChartUpdate:
    return ChartUpdate(
        song_title=title, difficulty="MAS", old_score=old, new_score=new, old_rank=old_rank,
        new_rank=new_rank, old_rating=Decimal(old_rating) if old_rating is not None else None,
        new_rating=Decimal(new_rating), session_id=session, estimated_session=estimated,
        entered_frame=entered,
    )


def _sync_diff(
    *,
    sessions: list[SessionInfo] | None = None,
    updates: list[ChartUpdate] | None = None,
    unchanged: list[UnchangedPlay] | None = None,
    exits: list[FrameExit] | None = None,
) -> SyncDiff:
    return SyncDiff(
        sync_id="sync-1",
        sessions=sessions if sessions is not None else [_S1],
        chart_updates=updates or [],
        unchanged_plays=unchanged or [],
        frame_exits=exits or [],
        previous_computed_rating=Decimal("16.90"),
        previous_best30_average=Decimal("17.00"),
        previous_new20_average=Decimal("16.75"),
    )


def _payload(*, has_new_plays: bool, focus: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "player_id": "me",
        "sync_id": "sync-1",
        "sessions": [{"id": "s1", "play_date": "2026-10-15", "estimated": False}],
        "has_new_plays": has_new_plays,
        "is_first_sync": False,
        "focus": focus,
        "previous_synced_at": "2026-10-14T12:00:00+00:00",
        "arcade_id": None,
    }


def _section_content(section: Section) -> str:
    assert isinstance(section.display, Display)
    return section.display.content or ""


async def test_sync_report_no_change_shows_current_values_with_change(skill: ModuleType) -> None:
    diff = _sync_diff(sessions=[])
    f = _ctx(records=_FakeRecords(diffs={"sync-1": diff}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    section = await skill.on_sync_completed(f.ctx, _payload(has_new_plays=False))

    assert isinstance(section, Section)
    assert section.order == 10
    assert section.stage is None
    assert section.hint is None
    content = _section_content(section)
    assert "변화 없음" in content
    assert "세션 요약" not in content and "갱신" not in content
    assert "16.94" in content and "17.02" in content and "16.83" in content
    # 지표는 현재값과 변화량을 함께
    assert "+0.04" in content and "+0.02" in content and "+0.08" in content
    assert section.data["has_new_plays"] is False
    assert section.data["current"]["computed_rating"] == "16.94"
    assert section.data["change"]["computed_rating"] == "+0.04"


async def test_sync_report_with_updates_has_summary_list_and_great_stage(skill: ModuleType) -> None:
    updates = [
        _update("곡 A", old=1_006_800, new=1_007_620, old_rank="SS+", new_rank="SSS",
                old_rating="17.16", new_rating="17.31", entered=True),
        _update("곡 B", old=1_008_100, new=1_008_700, old_rank="SSS", new_rank="SSS",
                old_rating="16.96", new_rating="17.02"),
        _update("곡 N", old=None, new=1_000_000, old_rank=None, new_rank="SS",
                old_rating=None, new_rating="15.50", estimated=True),
    ]
    diff = _sync_diff(updates=updates, exits=[FrameExit(song_title="곡 X", difficulty="MAS", old_rating=Decimal("16.50"))])
    f = _ctx(
        records=_FakeRecords(diffs={"sync-1": diff}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}),
        consts={("곡 A", "MAS"): 15.3, ("곡 B", "MAS"): 14.9},
    )

    section = await skill.on_sync_completed(f.ctx, _payload(has_new_plays=True))

    assert section.order == 10
    assert section.stage == "great"
    assert section.hint is not None and "SSS" in section.hint and "프레임 진입 1개" in section.hint
    content = _section_content(section)
    assert "세션 요약" in content and "10/15" in content
    assert "16.90 → 16.94" in content and "(+0.04)" in content
    assert "갱신 3채보" in content
    assert "곡 A MAS 15.3" in content and "1,006,800 → 1,007,620" in content and "SS+ → SSS" in content
    assert "17.16 → 17.31" in content
    assert "SSS 유지" in content
    assert "신규" in content
    assert "프레임 진입" in content
    assert "곡 X" in content  # 프레임 이탈
    assert [u["song_title"] for u in section.data["chart_updates"]] == ["곡 A", "곡 B", "곡 N"]
    assert section.data["chart_updates"][0]["new_rating"] == "17.31"
    assert section.data["stage"] == "great"


async def test_sync_report_good_stage_without_sss(skill: ModuleType) -> None:
    diff = _sync_diff(updates=[
        _update("곡 A", old=1_000_000, new=1_003_000, old_rank="SS", new_rank="SS",
                old_rating="16.00", new_rating="16.20"),
    ])
    f = _ctx(records=_FakeRecords(diffs={"sync-1": diff}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    section = await skill.on_sync_completed(f.ctx, _payload(has_new_plays=True))

    assert section.stage == "good"
    assert section.hint is None


async def test_sync_report_close_stage_when_played_without_update(skill: ModuleType) -> None:
    diff = _sync_diff(unchanged=[
        UnchangedPlay(song_title="곡 A", difficulty="MAS", play_count=3, current_score=1_005_000, session_id="s1"),
    ])
    f = _ctx(records=_FakeRecords(diffs={"sync-1": diff}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    section = await skill.on_sync_completed(f.ctx, _payload(has_new_plays=True))

    assert section.stage == "close"
    assert "갱신 0채보" in _section_content(section)


async def test_sync_report_splits_update_list_by_date(skill: ModuleType) -> None:
    diff = _sync_diff(
        sessions=[_S1, _S2],
        updates=[
            _update("곡 A", old=1_000_000, new=1_003_000, old_rank="SS", new_rank="SS",
                    old_rating="16.00", new_rating="16.20", session="s1"),
            _update("곡 B", old=1_000_000, new=1_004_000, old_rank="SS", new_rank="SS",
                    old_rating="16.00", new_rating="16.30", session="s2", estimated=True),
        ],
    )
    f = _ctx(records=_FakeRecords(diffs={"sync-1": diff}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    section = await skill.on_sync_completed(f.ctx, _payload(has_new_plays=True))

    content = _section_content(section)
    # 레이팅 숫자는 동기화 전체 기준으로 한 번만, 갱신 목록은 날짜별로 나눈다.
    assert content.count("16.90 → 16.94") == 1
    first, second = content.index("갱신  10/15"), content.index("갱신  10/16 (추정)")
    assert first < content.index("곡 A") < second < content.index("곡 B")
    assert [s["updated_count"] for s in section.data["sessions"]] == [1, 1]


async def test_sync_report_focus_filters_update_list_but_keeps_summary(skill: ModuleType) -> None:
    updates = [
        _update("곡 A", old=1_006_800, new=1_007_620, old_rank="SS+", new_rank="SSS",
                old_rating="17.16", new_rating="17.31"),
        _update("Chronomia", old=1_000_000, new=1_003_000, old_rank="SS", new_rank="SS",
                old_rating="16.00", new_rating="16.20"),
    ]
    diff = _sync_diff(updates=updates)
    f = _ctx(records=_FakeRecords(diffs={"sync-1": diff}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    section = await skill.on_sync_completed(
        f.ctx, _payload(has_new_plays=True, focus=[{"song_title": "Chronomia", "difficulty": "MAS"}])
    )

    content = _section_content(section)
    assert "Chronomia" in content
    assert "곡 A" not in content
    assert "갱신 2채보" in content  # 세션 요약은 그대로
    assert "16.90 → 16.94" in content
    assert [u["song_title"] for u in section.data["chart_updates"]] == ["Chronomia"]
    assert section.data["chart_updates_total"] == 2


async def test_sync_report_without_diff_falls_back_to_current_values(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    section = await skill.on_sync_completed(f.ctx, _payload(has_new_plays=True))

    assert section.order == 10
    assert section.stage is None
    content = _section_content(section)
    assert "16.94" in content
    assert "갱신" not in content
