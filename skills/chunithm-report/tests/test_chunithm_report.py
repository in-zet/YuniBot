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
from core.tables import TableBuilder, WorkTable, render_text
from core.tool_types import Display, ToolContext, ToolResult
from services.chunithm import table_columns as tc
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.records import (
    CandidateHit,
    ChartUpdate,
    FrameExit,
    PbRow,
    PlayerProfile,
    RatingDeltas,
    SessionInfo,
    SyncDiff,
    SyncLogSummary,
    UnchangedPlay,
)
from services.chunithm.songdb import ChartRecord, SongRecord
from services.chunithm.terms import TermsService
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
    profiles: dict[str, PlayerProfile] = field(default_factory=dict)

    async def player_profile(self, player_id: str) -> PlayerProfile | None:
        return self.profiles.get(player_id)

    async def pb_rows_for(self, player_id: str, keys: list[tuple[str, str]]) -> dict[tuple[str, str], PbRow]:
        wanted = set(keys)
        return {
            (r.song_title, r.difficulty): r
            for r in self.pbs.get(player_id, [])
            if (r.song_title, r.difficulty) in wanted
        }

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
        self.ctx_passed: list[ToolContext | None] = []  # 만료 처리를 위해 ctx를 넘겼는지

    async def get_recent_scores(self, player_id: str, *, ctx: ToolContext | None = None) -> list[RecentScore]:
        self.ctx_passed.append(ctx)
        if self._error is not None:
            raise self._error
        return list(self._scores)

    async def enrich_recent_with_detail(
        self, player_id: str, scores: list[RecentScore], *, limit: int, ctx: ToolContext | None = None
    ) -> list[RecentScore]:
        self.ctx_passed.append(ctx)
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
    songdb: Any = None,
) -> _Fakes:
    records = records or _FakeRecords()
    net = net or _FakeChunithmNet([])
    ctx = ToolContext(
        player_id="me",
        tags=frozenset({"chunithm-synced"}),
        services={
            "records": records,
            "songdb": songdb if songdb is not None else _FakeSongDb(consts),
            "players": _FakePlayers(players or [_player("me", public=False)]),
            "chunithm_net": net,
            "terms": TermsService(),
        },
    )
    return _Fakes(ctx=ctx, records=records, net=net)


def _content(result: ToolResult) -> str:
    assert result.display is not None
    return result.display.content or ""


def _chart(title: str = "Chronomia", difficulty: str = "MAS") -> ResolvedChart:
    song = SongRecord(title=title, aliases=(), artist="테스터", id=1)
    return ResolvedChart(song=song, chart=ChartRecord(difficulty=difficulty, const=14.8, note_count=2000))


# ---------------------------------------------------------------- profile


async def test_profile_self_ignores_privacy_setting(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}), players=[_player("me", public=False)])

    result = await skill.profile(f.ctx)

    assert result.error is None
    assert result.display_mode == "optional"
    assert result.data["computed_rating"] == "16.9400"
    assert result.data["best30_average"] == "17.0200"
    assert result.data["new20_average"] == "16.8300"
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


async def test_profile_does_not_show_change_since_previous_sync(skill: ModuleType) -> None:
    """변화량은 동기화 리포트 전용이다(사용자 확인 2026-10-18) — 프로필은 지금 값만 낸다."""
    f = _ctx(records=_FakeRecords(summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    result = await skill.profile(f.ctx)

    assert "change" not in result.data
    assert "previous" not in result.data
    assert "has_previous" not in result.data
    content = _content(result)
    assert "16.94" in content and "17.02" in content and "16.83" in content
    assert "+0.04" not in content and "비교할 직전 기록 없음" not in content


async def test_profile_never_synced(skill: ModuleType) -> None:
    f = _ctx()

    result = await skill.profile(f.ctx)

    assert result.error is not None and "동기화" in result.error


# ---------------------------------------------------------------- best


def _frames() -> tuple[list[PbRow], list[PbRow]]:
    best = [_pb("곡 A", "MAS", 1_007_620, "SSS", 15.3, "17.31"), _pb("곡 B", "MAS", 1_008_700, "SSS", 14.9, "17.02")]
    new = [_pb("신곡 C", "EXP", 1_009_000, "SSS+", 14.0, "16.15")]
    return best, new


async def test_best_never_synced(skill: ModuleType) -> None:
    f = _ctx()

    result = await skill.best(f.ctx)

    assert result.error is not None and "동기화" in result.error


def _titles_of(table: WorkTable | None) -> list[str]:
    assert table is not None
    return [str(row.cell(tc.SONG).raw) for row in table.rows]


async def test_best_both_returns_table_with_groups_and_contribution(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(frames={"me": _frames()}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    result = await skill.best(f.ctx)

    assert result.error is None and result.display is None
    table = result.table
    assert table is not None
    assert table.subject == "me" and table.unit == "채보"
    assert [g.title for g in table.groups] == ["베스트 30", "신곡 20"]
    assert [g.summary["평균"] for g in table.groups] == ["17.0200", "16.8300"]
    assert _titles_of(table) == ["곡 A", "곡 B", "신곡 C"]
    assert table.summary["닉네임"] == "펭귄" and table.summary["레이팅"] == "16.9400"
    assert "레벨" not in table.summary
    first = table.rows[0]
    assert first.ref.kind == tc.CHART and tc.chart_key(first.ref) == ("곡 A", "MAS")
    assert first.cell("기여").text == "0.3462"
    assert first.cell(tc.SCORE).raw == 1_007_620
    assert first.cell(tc.RANK).raw == 1_007_500  # 랭크 칸의 원래 값은 기준 점수
    assert first.cell(tc.DIFFICULTY).raw == "MASTER" and first.cell(tc.DIFFICULTY).text == "MAS"
    assert first.cell(tc.LAMP).raw == "FULL COMBO"
    assert first.cell(tc.RATING).raw == 17.31


async def test_best_text_line_keeps_the_old_shape_and_hides_lamp(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(frames={"me": _frames()}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    result = await skill.best(f.ctx)

    assert result.table is not None
    text = render_text(result.table).content
    assert "1. 곡 A MAS 15.3  1,007,620 SSS  곡 레이팅 17.31  기여 0.3462" in text
    assert "FULL COMBO" not in text
    assert "── 베스트 30 · 2채보 · 평균 17.0200 ──" in text
    assert "닉네임 펭귄 · 레이팅 16.94" in text


async def test_best_shows_level_when_profile_was_saved(skill: ModuleType) -> None:
    profile = PlayerProfile(
        level=62, reincarnation_stars=None, character_image_url=None, character_frame_url=None,
        updated_at="2026-10-17",
    )
    f = _ctx(records=_FakeRecords(frames={"me": _frames()}, summaries={"me": _SUMMARY}, profiles={"me": profile}))

    result = await skill.best(f.ctx)

    assert result.table is not None and result.table.summary["레벨"] == "62"


async def test_best_does_not_show_average_changes(skill: ModuleType) -> None:
    """변화량은 동기화 리포트 전용이다(사용자 확인 2026-10-18) — 베스트도 지금 값만 낸다."""
    f = _ctx(records=_FakeRecords(frames={"me": _frames()}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    result = await skill.best(f.ctx)

    assert result.table is not None
    text = render_text(result.table).content
    assert "+0.04" not in text and "비교할 직전 기록 없음" not in text


async def test_best_kind_new_only(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(frames={"me": _frames()}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    result = await skill.best(f.ctx, kind="new")

    assert result.table is not None
    assert [g.title for g in result.table.groups] == ["신곡 20"]
    assert _titles_of(result.table) == ["신곡 C"]


async def test_best_invalid_kind_is_error(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(frames={"me": _frames()}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    result = await skill.best(f.ctx, kind="전부")

    assert result.error is not None


async def test_best_of_private_other_is_refused(skill: ModuleType) -> None:
    other = _player("other", public=False)
    f = _ctx(records=_FakeRecords(frames={"other": _frames()}, deltas={"other": _DELTAS_SECOND}), players=[_player("me"), other])

    result = await skill.best(f.ctx, player=other)

    assert result.error is not None and "비공개" in result.error


# ---------------------------------------------------------------- add_scores


def _chart_table(*charts: tuple[str | None, str | None]) -> WorkTable:
    """(곡 정식 제목 또는 None, 난이도 코드 또는 None)마다 한 행. 곡 없는 행·곡 단위 행도 만들 수 있다."""
    builder = TableBuilder(subject=None, unit="채보")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY)
    builder.step("테스트 표")
    for title, difficulty in charts:
        ref = tc.song_ref(title) if difficulty is None and title else tc.chart_ref(title or "", difficulty or "")
        builder.row(ref, {tc.SONG: tc.song_cell(title), tc.DIFFICULTY: tc.difficulty_cell(difficulty)})
    return builder.build()


def _records_with_pbs() -> _FakeRecords:
    return _FakeRecords(
        pbs={"me": [_pb("곡 A", "MAS", 1_007_620, "SSS", 15.3, "17.31")], "other": [_pb("곡 B", "MAS", 990_000, "S+", 14.9, "14.90")]},
        summaries={"other": SyncLogSummary(
            nickname="상대", computed_rating=Decimal("15.00"), best30_average=None, new20_average=None,
            last_synced_at="2026-10-17T03:00:00+00:00",
        )},
    )


async def test_add_scores_adds_pb_columns_and_marks_missing_records(skill: ModuleType) -> None:
    f = _ctx(records=_records_with_pbs())

    result = await skill.add_scores(f.ctx, table=_chart_table(("곡 A", "MAS"), ("곡 B", "MAS")))

    assert result.error is None
    table = result.table
    assert table is not None
    assert [c.name for c in table.columns] == [tc.SONG, tc.DIFFICULTY, tc.SCORE, tc.RANK, tc.LAMP, tc.RATING]
    assert table.subject == "me" and table.steps == ("테스트 표", "내 점수")
    have, missing = table.rows
    assert have.cell(tc.SCORE).raw == 1_007_620 and have.cell(tc.RANK).text == "SSS"
    assert missing.cell(tc.SCORE).raw is None and missing.cell(tc.SCORE).text == "기록 없음"
    assert missing.cell(tc.LAMP).raw is None
    assert result.data["with_record"] == 1


async def test_add_scores_keeps_original_rows_and_refs(skill: ModuleType) -> None:
    f = _ctx(records=_records_with_pbs())
    source = _chart_table(("곡 A", "MAS"))

    result = await skill.add_scores(f.ctx, table=source)

    assert result.table is not None
    assert result.table.rows[0].ref == source.rows[0].ref
    assert source.column_by_name(tc.SCORE) is None  # 원래 표는 바뀌지 않는다


async def test_add_scores_uses_the_other_players_records_when_public(skill: ModuleType) -> None:
    other = _player("other", public=True)
    f = _ctx(records=_records_with_pbs(), players=[_player("me"), other])

    result = await skill.add_scores(f.ctx, table=_chart_table(("곡 A", "MAS"), ("곡 B", "MAS")), player=other)

    assert result.table is not None
    assert result.table.subject == "other"
    assert result.table.steps[-1] == "상대 점수"
    assert [r.cell(tc.SCORE).raw for r in result.table.rows] == [None, 990_000]


async def test_add_scores_refuses_private_other(skill: ModuleType) -> None:
    other = _player("other", public=False)
    f = _ctx(records=_records_with_pbs(), players=[_player("me"), other])

    result = await skill.add_scores(f.ctx, table=_chart_table(("곡 A", "MAS")), player=other)

    assert result.error is not None and "비공개" in result.error
    assert result.table is None


async def test_add_scores_twice_overwrites_instead_of_duplicating_columns(skill: ModuleType) -> None:
    f = _ctx(records=_records_with_pbs())
    first = await skill.add_scores(f.ctx, table=_chart_table(("곡 A", "MAS")))
    assert first.table is not None

    second = await skill.add_scores(f.ctx, table=first.table)

    assert second.table is not None
    assert [c.name for c in second.table.columns].count(tc.SCORE) == 1


def test_add_scores_declares_song_and_difficulty_needs() -> None:
    from core.schema import build_arg_schema

    skill = _load_skill()
    table_arg = next(a for a in build_arg_schema(skill.add_scores).args if a.name == "table")
    assert table_arg.table_needs is not None
    assert table_arg.table_needs.nonempty == (tc.SONG, tc.DIFFICULTY)


# ---------------------------------------------------------------- recent


async def test_recent_enriches_only_first_twenty_with_judgements(skill: ModuleType) -> None:
    scores = [_recent(f"곡{i}", 1_000_000 + i) for i in range(25)]
    f = _ctx(net=_FakeChunithmNet(scores))

    result = await skill.recent(f.ctx, count=25)

    assert result.error is None
    assert f.net.enrich_calls == [("me", 25, 20)]
    table = result.table
    assert table is not None and table.row_count == 25 and table.subject == "me"
    assert [c.label for c in table.columns] == ["곡", "난이도", "점수", "랭크", "JC", "J", "A", "M", "플레이 시각"]
    rows = table.rows
    assert all(r.cell("JC").filled for r in rows[:20])
    assert not any(r.cell("JC").filled for r in rows[20:])
    assert [rows[0].cell(n).raw for n in ("JC", "J", "A", "M")] == [1990, 8, 2, 0]
    assert table.summary["판정 수"] == "앞 20개에만 있음"
    assert result.data["detail_limit"] == 20
    assert "plays" not in result.data
    # 두 호출 모두 ctx를 넘긴다 — 서비스가 인증 실패 때 만료 처리(태그 해제·이벤트)를 할 수 있게.
    assert f.net.ctx_passed == [f.ctx, f.ctx]


async def test_recent_rows_keep_the_old_line_shape(skill: ModuleType) -> None:
    f = _ctx(net=_FakeChunithmNet([_recent("곡0", 1_007_500), _recent("곡1", 1_000_000)]))

    result = await skill.recent(f.ctx, count=1)
    assert result.table is not None
    assert render_text(result.table).content.splitlines()[-1] == "1. 곡0 MAS  1,007,500 SSS  JC 1990 / J 8 / A 2 / M 0"

    f = _ctx(net=_FakeChunithmNet([_recent("곡0", 1_007_500)] * 25))
    result = await skill.recent(f.ctx, count=25)
    assert result.table is not None
    lines = render_text(result.table, slash=True)
    assert lines.file is not None
    body = lines.file[1].decode()
    assert "21. 곡0 MAS  1,007,500 SSS" in body and "21. 곡0 MAS  1,007,500 SSS  JC" not in body
    assert "플레이 시각" not in body and "10/16" not in body  # 플레이 시각은 글자 출력에서 숨긴다


async def test_recent_same_chart_can_repeat_with_distinct_refs(skill: ModuleType) -> None:
    f = _ctx(net=_FakeChunithmNet([_recent("곡", 1_000_000), _recent("곡", 1_000_000)]))

    result = await skill.recent(f.ctx, count=2)

    assert result.table is not None
    keys = [r.ref.key for r in result.table.rows]
    assert len(set(keys)) == 2 and all(r.ref.kind == "chunithm-report.play" for r in result.table.rows)
    assert result.table.rows[0].cell("플레이 시각").text == "10/16 20:00"


async def test_recent_empty(skill: ModuleType) -> None:
    f = _ctx(net=_FakeChunithmNet([]))

    result = await skill.recent(f.ctx, count=5)

    assert result.error is None and result.table is None
    assert "없" in _content(result)


async def test_recent_count_limits_shown_plays(skill: ModuleType) -> None:
    scores = [_recent(f"곡{i}", 1_000_000 + i) for i in range(30)]
    f = _ctx(net=_FakeChunithmNet(scores))

    result = await skill.recent(f.ctx, count=5)

    assert result.table is not None and result.table.row_count == 5
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


# ---------------------------------------------------------------- record


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


# ---------------------------------------------------------------- analyze


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
    table = result.table
    assert table is not None and table.subject == "me" and table.unit == "상수대"
    bands = {int(r.cell("상수대").raw): r for r in table.rows}
    assert list(bands) == [15, 14]  # 높은 상수대부터
    assert bands[15].cell("채보 수").raw == 2
    assert bands[15].cell("평균 점수").raw == 1_008_000
    assert bands[15].cell("랭크 분포").text == "SSS+ 1, SS+ 1"
    assert bands[14].cell("평균 점수").raw == 995_000  # (1,000,000 + 990,001) // 2
    assert bands[14].cell("랭크 분포").text == "SS 1, S+ 1"
    assert table.summary["상수를 몰라 묶지 못한 채보"] == "1개"
    assert result.data["unknown_const_count"] == 1
    assert render_text(table).content.splitlines()[-2:] == [
        "1. 15대  2채보  평균 1,008,000  (SSS+ 1, SS+ 1)",
        "2. 14대  2채보  평균 995,000  (SS 1, S+ 1)",
    ]
    assert "attack" not in str(result.data).lower()  # 어택·미스 비중은 없다(chunithm-report.md "분석")


async def test_analyze_const_range_filter(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(pbs={"me": _analyze_rows()}))

    result = await skill.analyze(f.ctx, min_const=15.0, max_const=15.5)

    table = result.table
    assert table is not None
    assert [r.cell("상수대").raw for r in table.rows] == [15]
    assert table.rows[0].cell("채보 수").raw == 1
    assert table.rows[0].cell("평균 점수").raw == 1_007_000
    assert "15.0~15.5" in table.steps[0]
    assert "상수를 몰라 묶지 못한 채보" not in table.summary  # 범위를 주면 모르는 상수는 세지 않는다


async def test_analyze_with_table_uses_only_that_tables_charts(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(pbs={"me": _analyze_rows()}))
    source = _chart_table(("a", "MAS"), ("c", "MAS"), ("d", "EXP"), ("기록 없는 곡", "MAS"))

    result = await skill.analyze(f.ctx, table=source)

    assert result.error is None and result.table is not None
    bands = {int(r.cell("상수대").raw): r for r in result.table.rows}
    assert list(bands) == [15, 14]
    assert bands[15].cell("채보 수").raw == 1 and bands[15].cell("평균 점수").raw == 1_007_000
    assert bands[14].cell("채보 수").raw == 2
    assert result.table.steps[-1] == "표의 4채보 중 기록 있는 3채보만"
    assert "상수를 몰라 묶지 못한 채보" not in result.table.summary


async def test_analyze_with_table_errors_when_no_chart_has_a_record(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(pbs={"me": _analyze_rows()}))

    result = await skill.analyze(f.ctx, table=_chart_table(("기록 없는 곡", "MAS")))

    assert result.table is None and result.error is not None and "기록" in result.error


async def test_analyze_slash_entry_has_no_table_argument(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(pbs={"me": _analyze_rows()}))

    result = await skill.analyze_slash(f.ctx)

    assert result.table is not None and result.table.unit == "상수대"
    assert skill.analyze_slash.__yuni_slash_only__.slash == "분석"


async def test_analyze_empty_range_is_a_message(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(pbs={"me": _analyze_rows()}))

    result = await skill.analyze(f.ctx, min_const=17.0)

    assert result.table is None
    assert "없" in _content(result)


async def test_analyze_of_private_other_is_refused(skill: ModuleType) -> None:
    other = _player("other", public=False)
    f = _ctx(records=_FakeRecords(pbs={"other": _analyze_rows()}), players=[_player("me"), other])

    result = await skill.analyze(f.ctx, player=other)

    assert result.error is not None and "비공개" in result.error


# ---------------------------------------------------------------- candidates


def _hit(title: str, upside: str, *, remaining: int | None = 7_500) -> CandidateHit:
    return CandidateHit(
        song_title=title, difficulty="MAS", const=15.0, upside=Decimal(upside), current_score=1_000_000,
        remaining_score=remaining,
    )


async def test_candidates_passes_range_and_count_for_self(skill: ModuleType) -> None:
    hits = [_hit("곡 A", "0.50"), _hit("곡 B", "0.30", remaining=None)]
    f = _ctx(records=_FakeRecords(hits=hits))

    result = await skill.candidates(f.ctx, min_const=14.5, max_const=15.5, count=5)

    assert f.records.candidate_calls == [{"player_id": "me", "min_const": 14.5, "max_const": 15.5, "count": 5}]
    table = result.table
    assert table is not None and table.subject == "me"
    assert [r.cell("곡").raw for r in table.rows] == ["곡 A", "곡 B"]  # 순서는 상승폭 큰 순 그대로
    assert [r.cell("상승폭").text for r in table.rows] == ["0.50", "0.30"]
    assert table.rows[0].cell("SSS까지 남은 점수").raw == 7_500
    assert table.rows[1].cell("SSS까지 남은 점수").raw is None
    assert "14.5~15.5" in table.steps[0]
    assert table.summary["SSS까지 남은 점수"] == "1채보는 계산 불가"
    assert render_text(table).content.splitlines()[-2:] == [
        "1. 곡 A MAS 15.0  현재 1,000,000  SSS까지 7,500점  상승폭 +0.50",
        "2. 곡 B MAS 15.0  현재 1,000,000  상승폭 +0.30",
    ]
    assert result.data["count"] == 2 and "candidates" not in result.data


async def test_candidates_empty(skill: ModuleType) -> None:
    f = _ctx()

    result = await skill.candidates(f.ctx)

    assert result.error is None
    assert result.table is None
    assert result.data["count"] == 0
    assert "없" in _content(result)


# ---------------------------------------------------------------- 동기화 리포트 조각

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
        # 리포트는 이 필드들 대신 records.rating_deltas(/프로필·/베스트와 같은 출처)를 쓴다 —
        # 일부러 _DELTAS_SECOND와 다른 값을 둬서, 숫자가 이쪽에서 새어 나오면 테스트가 잡게 한다.
        previous_computed_rating=Decimal("15.00"),
        previous_best30_average=Decimal("15.10"),
        previous_new20_average=Decimal("15.20"),
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
    assert "+0.04" in content and "+0.02" in content and "+0.00" in content
    assert section.data["has_new_plays"] is False
    assert section.data["current"]["computed_rating"] == "16.9400"
    assert section.data["change"]["computed_rating"] == "+0.0400"


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
    assert "16.9000 → 16.9400" in content and "(+0.0400)" in content
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
    assert content.count("16.9000 → 16.9400") == 1
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
    assert "16.9000 → 16.9400" in content
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


async def test_sync_report_metrics_match_rating_deltas_exactly(skill: ModuleType) -> None:
    """리포트의 세 지표 현재·직전 값은 /프로필·/베스트와 같은 rating_deltas(동기화 시점 스냅샷)에서
    온다 — 재계산 쪽(latest_sync_summary, diff.previous_*)과 달라도 rating_deltas를 따른다
    (예: Chunimaru 덧씌우기 전이라 재계산 신곡20이 비어 있는 경우)."""
    deltas = RatingDeltas(
        computed_rating=Decimal("16.94"), best30_average=Decimal("17.02"), new20_average=Decimal("16.83"),
        has_previous=True, previous_computed_rating=Decimal("16.90"),
        previous_best30_average=Decimal("17.00"), previous_new20_average=Decimal("16.79"),
    )
    recomputed = SyncLogSummary(
        nickname="펭귄", computed_rating=Decimal("16.94"), best30_average=Decimal("17.02"),
        new20_average=None, last_synced_at="2026-10-17T03:00:00+00:00",
    )
    diff = _sync_diff(updates=[
        _update("곡 A", old=1_000_000, new=1_003_000, old_rank="SS", new_rank="SS",
                old_rating="16.00", new_rating="16.20"),
    ])
    f = _ctx(records=_FakeRecords(diffs={"sync-1": diff}, summaries={"me": recomputed}, deltas={"me": deltas}))

    for has_new_plays in (True, False):
        section = await skill.on_sync_completed(f.ctx, _payload(has_new_plays=has_new_plays))
        assert section.data["current"] == {
            "computed_rating": "16.9400", "best30_average": "17.0200", "new20_average": "16.8300",
        }
        assert section.data["previous"] == {
            "computed_rating": "16.9000", "best30_average": "17.0000", "new20_average": "16.7900",
        }
        assert section.data["change"] == {
            "computed_rating": "+0.0400", "best30_average": "+0.0200", "new20_average": "+0.0400",
        }
        content = _section_content(section)
        assert "16.7900 → 16.8300" in content
        assert "15.20" not in content and "계산 불가" not in content


async def test_sync_report_shows_pb_missing_from_recent_history_as_estimated(skill: ModuleType) -> None:
    """크레딧 직후 동기화로 최근 이력엔 안 보이지만 PB가 오른 경우: records가 has_new_plays=True로
    올려 주므로 리포트는 "변화 없음"으로 숨지 않고 추정 세션의 갱신으로 보여 준다."""
    estimated_today = SessionInfo(id="s-est", play_date="2026-10-17", estimated=True)
    diff = _sync_diff(
        sessions=[estimated_today],
        updates=[
            _update("곡 A", old=1_000_000, new=1_003_000, old_rank="SS", new_rank="SS",
                    old_rating="16.00", new_rating="16.20", session="s-est", estimated=True),
        ],
    )
    f = _ctx(records=_FakeRecords(diffs={"sync-1": diff}, summaries={"me": _SUMMARY}, deltas={"me": _DELTAS_SECOND}))

    section = await skill.on_sync_completed(f.ctx, _payload(has_new_plays=True))

    content = _section_content(section)
    assert "변화 없음" not in content
    assert "세션 요약  10/17 (추정)" in content
    assert "갱신 1채보" in content
    assert "곡 A MAS" in content and "1,000,000 → 1,003,000" in content
    assert section.stage == "good"
    assert section.data["has_new_plays"] is True
    assert section.data["chart_updates"][0]["estimated_session"] is True
    assert section.data["sessions"][0]["estimated"] is True


# ---------------------------------------------------------------- const_table


def _real_songdb() -> Any:
    from services.chunithm.songdb import SongDbService

    return SongDbService()


def _diff(canonical: str) -> Any:
    from services.chunithm.terms import ResolvedDifficulty

    return ResolvedDifficulty(canonical=canonical)


def _scored(hit: Any, score: int) -> PbRow:
    return _pb(hit.song.title, hit.chart.difficulty, score, "SS", hit.chart.const, "16.00")


def _row_keys(table: WorkTable) -> list[tuple[str, str]]:
    return [(str(r.cell(tc.SONG).raw), str(r.cell(tc.DIFFICULTY).text)) for r in table.rows]


async def test_const_table_groups_by_const_desc_then_difficulty_desc_then_score_desc(skill: ModuleType) -> None:
    songdb = _real_songdb()
    hits = songdb.find_songs(min_const=15.0, max_const=15.0)
    mas = [h for h in hits if h.chart.difficulty == "MAS"]
    ult = [h for h in hits if h.chart.difficulty == "ULT"]
    assert len(mas) >= 3 and ult
    pbs = [_scored(mas[0], 1_000_000), _scored(mas[1], 1_005_000), _scored(mas[2], 990_000), _scored(ult[0], 980_000)]
    f = _ctx(records=_FakeRecords(pbs={"me": pbs}), songdb=songdb)

    result = await skill.const_table(f.ctx, min_const=15.0, max_const=15.0)

    table = result.table
    assert result.error is None and table is not None
    assert [g.title for g in table.groups] == ["15.0"]
    keys = _row_keys(table)
    assert [d for _, d in keys] == sorted((d for _, d in keys), key=lambda d: -tc.DIFFICULTY_ORDER.index(tc.SHORT_TO_LONG_DIFFICULTY[d]))
    ult_rows = [k for k in keys if k[1] == "ULT"]
    assert keys[: len(ult_rows)] == ult_rows and (ult[0].song.title, "ULT") == ult_rows[0]  # ULT 안에서는 점수 있는 곡이 먼저
    mas_keys = [k for k in keys if k[1] == "MAS"]
    assert mas_keys[:3] == [(mas[1].song.title, "MAS"), (mas[0].song.title, "MAS"), (mas[2].song.title, "MAS")]  # 점수 내림차순, 기록 없음은 뒤
    assert table.steps == ("15.0~15.0 상수", "내 점수")
    assert [c.label for c in table.columns] == ["곡", "난이도", "상수", "점수", "랭크", "램프", "곡 레이팅"]


async def test_const_table_groups_come_biggest_const_first_and_include_unplayed_by_default(skill: ModuleType) -> None:
    f = _ctx(records=_FakeRecords(), songdb=_real_songdb())

    result = await skill.const_table(f.ctx, min_const=14.9, max_const=15.1)

    table = result.table
    assert table is not None
    assert [g.title for g in table.groups] == ["15.1", "15.0", "14.9"]
    assert all(r.cell(tc.SCORE).text == "기록 없음" for r in table.rows)
    assert result.data["with_record"] == 0


async def test_const_table_only_played_difficulty_and_open_ended_range(skill: ModuleType) -> None:
    songdb = _real_songdb()
    hit = next(h for h in songdb.find_songs(min_const=15.5, difficulty="MAS"))
    f = _ctx(records=_FakeRecords(pbs={"me": [_scored(hit, 1_000_000)]}), songdb=songdb)

    played = await skill.const_table(f.ctx, min_const=15.5, difficulty=_diff("MASTER"), only_played=True)

    assert played.table is not None
    assert _row_keys(played.table) == [(hit.song.title, "MAS")]
    assert played.table.steps == ("15.5 이상 상수 MAS", "내 점수", "점수 있는 것만")
    empty = await skill.const_table(_ctx(records=_FakeRecords(), songdb=songdb).ctx, min_const=15.5, only_played=True)
    assert empty.table is None and "점수가 있는 채보가 없어" in _content(empty)
    nothing = await skill.const_table(f.ctx, min_const=99.0)
    assert nothing.table is None and "해당 범위의 채보가 없어" in _content(nothing)


async def test_const_table_other_player_needs_public_records(skill: ModuleType) -> None:
    other = _player("other", public=True)
    private = _player("hidden", public=False)
    f = _ctx(records=_records_with_pbs(), players=[_player("me", public=False), other, private], songdb=_real_songdb())

    ok = await skill.const_table(f.ctx, min_const=15.5, max_const=15.5, player=other)
    refused = await skill.const_table(f.ctx, min_const=15.5, max_const=15.5, player=private)

    assert ok.table is not None and ok.table.subject == "other" and ok.table.steps[1] == "상대 점수"
    assert refused.error == "그 사람은 기록을 비공개로 해 놨어."
