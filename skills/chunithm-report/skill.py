# skills/chunithm-report/skill.py
from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, NamedTuple

from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError
from chuni_penguin.types.score import RecentScore

from core.decorators import on, tool
from core.events import Section, Stage
from core.tables import (
    EMPTY_CELL,
    Cell,
    Row,
    RowRef,
    TableBuilder,
    TableNeeds,
    WorkTable,
    cell,
    extend_table,
)
from core.tool_types import (
    ChartRef,
    Display,
    PlayerRef,
    TableRef,
    ToolContext,
    ToolResult,
)
from services.chunithm import table_columns as tc
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.records import (
    ChartUpdate,
    PbRow,
    RatingDeltas,
    SyncDiff,
)
from services.chunithm.songdb import LONG_TO_SHORT_DIFFICULTY
from services.common.players import Player

# 판정 수(상세 판정)는 CHUNITHM-NET 플레이 상세 페이지를 한 곡씩 따로 열어야 붙는다.
# 설계서가 고정한 상한이다 — 바꾸지 않는다.
_DETAIL_LIMIT = 20
_UNKNOWN = "계산 불가"
_SSS_TIER = frozenset({"SSS", "SSS+"})
# analyze의 랭크 분포를 높은 랭크부터 보여 주는 순서.
_RANK_ORDER: tuple[str, ...] = (
    "SSS+", "SSS", "SS+", "SS", "S+", "S", "AAA", "AA", "A", "BBB", "BB", "B", "C", "D",
)
_PRIVATE_ERROR = "그 사람은 기록을 비공개로 해 놨어."
_NOT_SYNCED_ERROR = "아직 동기화한 기록이 없어. /동기화를 먼저 해 줘."
_REPORT_ORDER = 10


class BestKind(StrEnum):
    BEST = "best"
    NEW = "new"
    BOTH = "both"


# ---------------------------------------------------------------- 공통 도우미


async def _target_player(ctx: ToolContext, player: PlayerRef | None) -> Player | ToolResult:
    """조회 대상 플레이어. 안 넘어왔으면 본인이다(PlayerRef 해석기는 인자가 있을 때만 돈다).
    본인은 공개 설정과 무관하게 보고, 남은 records_public이 켜져 있을 때만 본다."""
    if player is None:
        me = await ctx.players.get_player(ctx.player_id)
        if me is None:
            return ToolResult(error="플레이어 정보를 찾지 못했어.")
        return me
    assert isinstance(player, Player)
    if player.id != ctx.player_id and not player.records_public:
        return ToolResult(error=_PRIVATE_ERROR)
    return player


def _num(value: Decimal | None) -> str:
    return _UNKNOWN if value is None else str(value)


def _delta(current: Decimal | None, previous: Decimal | None) -> str:
    if current is None or previous is None:
        return _UNKNOWN
    return f"{current - previous:+}"


def _score(value: int) -> str:
    return f"{value:,}"


def _const_text(const: float | None) -> str:
    return "" if const is None else f" {const}"


def _short_date(iso_date: str) -> str:
    """"2026-10-08" → "10/08"."""
    return f"{iso_date[5:7]}/{iso_date[8:10]}"


def _contribution(rating: Decimal | None) -> str:
    # 전체 레이팅 = (베스트30 + 신곡20 곡 레이팅 합) / 50. 곡 레이팅이 둘째 자리 값이라 /50은
    # 넷째 자리에서 정확히 끝난다(반올림이 필요 없다).
    return _UNKNOWN if rating is None else str(rating / 50)


def _const_range_texts(min_const: float | None, max_const: float | None) -> list[str]:
    """표의 과정(steps)에 적는 상수 범위."""
    if min_const is None and max_const is None:
        return []
    low = "" if min_const is None else f"{min_const:.1f}"
    high = "" if max_const is None else f"{max_const:.1f}"
    return [f"상수 {low}~{high}"]


def _pb_data(row: PbRow) -> dict[str, Any]:
    return {
        "song_title": row.song_title, "difficulty": row.difficulty, "score": row.score, "rank": row.rank,
        "clear_lamp": row.clear_lamp, "combo_lamp": row.combo_lamp, "chain_lamp": row.chain_lamp,
        "const": row.const, "rating": _num(row.rating),
    }


_METRIC_LABELS = {"computed_rating": "레이팅    ", "best30_average": "베스트 30 ", "new20_average": "신곡 20   "}


# ---------------------------------------------------------------- 도구


@tool(slash="프로필")
async def profile(ctx: ToolContext, player: PlayerRef | None = None) -> ToolResult:
    """닉네임, 레이팅·베스트 30 평균·신곡 20 평균(지금 값), 마지막 동기화 시각을 보여준다. player를 비우면 본인이다."""
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    summary = await ctx.records.latest_sync_summary(target.id)
    if summary is None:
        return ToolResult(error=_NOT_SYNCED_ERROR)

    # 직전 동기화 대비 변화량은 여기서 안 보여준다 — 동기화 리포트(세션 맥락) 전용이다
    # (사용자 확인 2026-10-18: 단순 조회에 변화량을 같이 보여줄 근거가 없었다).
    data: dict[str, Any] = {
        "player_id": target.id,
        "nickname": summary.nickname,
        "computed_rating": _num(summary.computed_rating),
        "best30_average": _num(summary.best30_average),
        "new20_average": _num(summary.new20_average),
        "last_synced_at": summary.last_synced_at,
    }
    lines = [
        f"{summary.nickname or '(닉네임 없음)'}",
        f"{_METRIC_LABELS['computed_rating']}  {_num(summary.computed_rating)}",
        f"{_METRIC_LABELS['best30_average']}  {_num(summary.best30_average)}",
        f"{_METRIC_LABELS['new20_average']}  {_num(summary.new20_average)}",
        f"마지막 동기화  {summary.last_synced_at}",
    ]
    return ToolResult(data=data, display=Display(content="\n".join(lines)), display_mode="optional")


_CONTRIBUTION = "기여"
_BEST_COLUMNS = (tc.SONG, tc.DIFFICULTY, tc.CONST, tc.SCORE, tc.RANK, tc.LAMP, tc.RATING)
# 한 줄 서식(램프는 글자 출력에서 숨긴다 — 줄 모양은 지금 베스트 줄 그대로).
_BEST_ROW_FORMAT = "{곡} {난이도} {상수}  {점수} {랭크}  곡 레이팅 {곡 레이팅}  기여 {기여}"


def _pb_row_cells(row: PbRow, rank_scores: dict[str, int]) -> dict[str, Cell]:
    cells = {
        tc.SONG: tc.song_cell(row.song_title),
        tc.DIFFICULTY: tc.difficulty_cell(row.difficulty),
        tc.CONST: tc.const_cell(row.const),
        **tc.pb_cells(row, rank_scores),
    }
    cells[_CONTRIBUTION] = (
        cell(float(row.rating / 50), _contribution(row.rating)) if row.rating is not None
        else cell(None, _UNKNOWN)
    )
    return cells


@tool(slash="베스트")
async def best(ctx: ToolContext, player: PlayerRef | None = None, kind: BestKind = BestKind.BOTH) -> ToolResult:
    """베스트 30과 신곡 20 표(채보, 점수, 곡 레이팅, 전체 레이팅 기여도)를 돌려준다. kind는 best(베스트 30), new(신곡 20), both(둘 다)."""
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    try:
        kind = BestKind(kind)
    except ValueError:
        return ToolResult(error="kind는 best, new, both 중 하나여야 해.")
    best_rows, new_rows = await ctx.records.current_frames(target.id)
    if not best_rows and not new_rows:
        return ToolResult(error=_NOT_SYNCED_ERROR)
    summary = await ctx.records.latest_sync_summary(target.id)
    if summary is None:
        return ToolResult(error=_NOT_SYNCED_ERROR)
    profile_row = await ctx.records.player_profile(target.id)

    # 변화량은 여기서 안 보여준다 — profile()과 같은 이유(동기화 리포트 전용).
    frames: list[tuple[str, list[PbRow], Decimal | None]] = []
    if kind in (BestKind.BEST, BestKind.BOTH):
        frames.append(("베스트 30", best_rows, summary.best30_average))
    if kind in (BestKind.NEW, BestKind.BOTH):
        frames.append(("신곡 20", new_rows, summary.new20_average))

    builder = TableBuilder(subject=target.id, unit="채보")
    tc.add_columns(builder, *_BEST_COLUMNS, hidden_in_text=(tc.LAMP,))
    builder.column(_CONTRIBUTION, value_type="number")
    builder.row_formats([_BEST_ROW_FORMAT])
    builder.step(" · ".join(title for title, _, _ in frames))
    header: dict[str, str] = {"닉네임": summary.nickname or "(닉네임 없음)"}
    if profile_row is not None and profile_row.level is not None:
        header["레벨"] = str(profile_row.level)
    header["레이팅"] = _num(summary.computed_rating)
    builder.summary(header)

    rank_scores = ctx.terms.rank_scores()
    for title, rows, average in frames:
        builder.group(title, {"평균": _num(average)})
        for r in rows:
            builder.row(tc.chart_ref(r.song_title, r.difficulty), _pb_row_cells(r, rank_scores))
    return ToolResult(
        data={"player_id": target.id, "kind": kind.value, "computed_rating": _num(summary.computed_rating)},
        table=builder.build(),
    )


_SCORE_COLUMNS = (tc.SCORE, tc.RANK, tc.LAMP, tc.RATING)


@tool()
async def add_scores(
    ctx: ToolContext,
    table: Annotated[TableRef, TableNeeds(nonempty=[tc.SONG, tc.DIFFICULTY])],
    player: PlayerRef | None = None,
) -> ToolResult:
    """채보 표에 점수·랭크·램프·곡 레이팅 칸을 더한 새 표를 돌려준다. player를 비우면 본인 기록이고, 기록이 없는 채보는 "기록 없음"이다."""
    assert isinstance(table, WorkTable)
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target

    def key_of(row: Row) -> tuple[str, str]:
        difficulty = str(row.cell(tc.DIFFICULTY).raw)
        return str(row.cell(tc.SONG).raw), LONG_TO_SHORT_DIFFICULTY.get(difficulty, difficulty)

    pbs = await ctx.records.pb_rows_for(target.id, [key_of(row) for row in table.rows])
    rank_scores = ctx.terms.rank_scores()

    if target.id == ctx.player_id:
        owner = "내 점수"
    else:
        other = await ctx.records.latest_sync_summary(target.id)
        owner = f"{other.nickname} 점수" if other is not None and other.nickname else "상대 점수"
    extended = extend_table(
        table,
        tc.common_columns(*_SCORE_COLUMNS),
        lambda row: tc.pb_cells(pbs.get(key_of(row)), rank_scores),
        steps=[owner],
    )
    return ToolResult(
        data={"player_id": target.id, "with_record": len(pbs)},
        table=replace(extended, subject=target.id),
    )


_CONST_BAND = "chunithm-report.const_band"
_BAND = "상수대"
_BAND_COUNT = "채보 수"
_BAND_AVERAGE = "평균 점수"
_BAND_RANKS = "랭크 분포"
_ANALYZE_FORMAT = "{상수대}대  {채보 수}채보  평균 {평균 점수}  ({랭크 분포})"
_REMAINING = "SSS까지 남은 점수"
_UPSIDE = "상승폭"
_CANDIDATE_FORMATS = (
    "{곡} {난이도} {상수}  현재 {점수}  SSS까지 {SSS까지 남은 점수}점  상승폭 +{상승폭}",
    "{곡} {난이도} {상수}  현재 {점수}  상승폭 +{상승폭}",
)
_PLAY = "chunithm-report.play"
_PLAY_TIME = "플레이 시각"
_JUDGEMENT_COLUMNS = ("JC", "J", "A", "M")
_RECENT_FORMATS = (
    "{곡} {난이도}  {점수} {랭크}  JC {JC} / J {J} / A {A} / M {M}",
    "{곡} {난이도}  {점수} {랭크}",
)


def _judgement_cells(score: RecentScore) -> dict[str, Cell]:
    """판정 수 칸. 판정 상세를 못 받은 플레이(앞 20개 밖)는 칸을 만들지 않는다."""
    j = score.judgements
    if j is None:
        return {}
    values = (j.justice_critical, j.justice, j.attack, j.miss)
    return {name: cell(value) for name, value in zip(_JUDGEMENT_COLUMNS, values, strict=True)}


@tool(slash="최근", cost="network")
async def recent(ctx: ToolContext, player: PlayerRef | None = None, count: int = 10) -> ToolResult:
    """CHUNITHM-NET 최근 플레이 이력을 count개 표로 보여준다. 판정 수는 앞 20개에만 붙는다."""
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    count = max(1, count)
    try:
        scores = await ctx.chunithm_net.get_recent_scores(target.id, ctx=ctx)
        # 보여 줄 만큼만 넘긴다 — 상세 판정은 곡마다 따로 요청하므로, 안 보여 줄 곡까지 열지 않는다.
        scores = await ctx.chunithm_net.enrich_recent_with_detail(
            target.id, scores[:count], limit=_DETAIL_LIMIT, ctx=ctx
        )
    except MaintenanceError:
        return ToolResult(error="CHUNITHM-NET이 점검 중이야. 점검이 끝나면 다시 해 줘.")
    except (AuthenticationError, ConnectionError):
        if target.id == ctx.player_id:
            return ToolResult(error="로그인이 풀렸어. /로그인으로 다시 연결해 줘.")
        return ToolResult(error="그 사람은 CHUNITHM-NET 로그인이 안 돼 있어서 최근 기록을 못 가져와.")

    if not scores:
        return ToolResult(data={"player_id": target.id, "count": 0}, display=Display(content="최근 플레이가 없어."))
    builder = TableBuilder(subject=target.id, unit="플레이")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY, tc.SCORE, tc.RANK)
    for name in _JUDGEMENT_COLUMNS:
        builder.column(name, value_type="number")
    builder.column(_PLAY_TIME, hidden_in_text=True)
    builder.row_formats(_RECENT_FORMATS)
    builder.step("최근 플레이")
    if len(scores) > _DETAIL_LIMIT:
        builder.summary({"판정 수": f"앞 {_DETAIL_LIMIT}개에만 있음"})
    rank_scores = ctx.terms.rank_scores()
    for number, score in enumerate(scores, start=1):
        difficulty = score.chart.difficulty.short()
        played = score.achieved_at.isoformat() if score.achieved_at is not None else ""
        key = json.dumps([number, played, score.song.title, difficulty], ensure_ascii=False)
        builder.row(
            RowRef(_PLAY, key),
            {
                tc.SONG: tc.song_cell(score.song.title),
                tc.DIFFICULTY: tc.difficulty_cell(difficulty),
                tc.SCORE: tc.score_cell(score.score),
                tc.RANK: tc.rank_cell(str(score.rank), rank_scores),
                **_judgement_cells(score),
                _PLAY_TIME: cell(played, f"{score.achieved_at:%m/%d %H:%M}") if played else EMPTY_CELL,
            },
        )
    return ToolResult(
        data={"player_id": target.id, "count": len(scores), "detail_limit": _DETAIL_LIMIT},
        table=builder.build(),
    )


@tool(slash="기록")
async def record(ctx: ToolContext, chart: ChartRef, player: PlayerRef | None = None) -> ToolResult:
    """채보 하나의 최고 기록(점수, 랭크, 램프, 곡 레이팅)을 보여준다. player를 비우면 본인이다."""
    assert isinstance(chart, ResolvedChart)
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    row = await ctx.records.pb_row(target.id, song_title=chart.song.title, difficulty=chart.chart.difficulty)
    if row is None:
        return ToolResult(error=f"{chart.song.title} {chart.chart.difficulty}는 플레이한 기록이 없어.")

    content = (
        f"{row.song_title} {row.difficulty}{_const_text(row.const)}\n"
        f"{_score(row.score)} {row.rank}  {row.clear_lamp} / {row.combo_lamp}\n"
        f"곡 레이팅 {_num(row.rating)}"
    )
    return ToolResult(
        data={"player_id": target.id, **_pb_data(row)},
        display=Display(content=content),
        display_mode="optional",
    )


class ConstBand(NamedTuple):
    band: int  # 상수의 정수 부분(15.3 → 15)
    chart_count: int
    average_score: int  # 버림
    ranks: dict[str, int]  # 높은 랭크부터


def const_band_stats(
    rows: list[PbRow], *, min_const: float | None, max_const: float | None
) -> tuple[list[ConstBand], int]:
    """상수대(정수 단위)별 채보 수·평균 점수(버림)·랭크 분포. 높은 상수대부터.
    상수를 모르는 채보는 묶지 않고 개수만 따로 돌려준다(상수 범위를 줬으면 그 개수도 0으로 친다)."""
    grouped: dict[int, list[PbRow]] = {}
    unknown = 0
    for row in rows:
        if row.const is None:
            unknown += 1
            continue
        if (min_const is not None and row.const < min_const) or (max_const is not None and row.const > max_const):
            continue
        grouped.setdefault(int(row.const), []).append(row)
    if min_const is not None or max_const is not None:
        unknown = 0

    bands: list[ConstBand] = []
    for band in sorted(grouped, reverse=True):
        members = grouped[band]
        counter = Counter(r.rank for r in members)
        ranks = {rank: counter[rank] for rank in _RANK_ORDER if counter[rank]}
        ranks.update({rank: n for rank, n in counter.items() if rank not in ranks})
        bands.append(
            ConstBand(
                band=band, chart_count=len(members), average_score=sum(r.score for r in members) // len(members),
                ranks=ranks,
            )
        )
    return bands, unknown


@tool(slash="분석")
async def analyze(
    ctx: ToolContext,
    player: PlayerRef | None = None,
    min_const: float | None = None,
    max_const: float | None = None,
) -> ToolResult:
    """플레이한 채보를 상수대(정수 단위)로 묶어 평균 점수와 랭크 분포를 보여준다. 상수 범위로 거를 수 있다."""
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    rows = await ctx.records.all_pb_rows(target.id)
    if not rows:
        return ToolResult(error=_NOT_SYNCED_ERROR)
    bands, unknown = const_band_stats(rows, min_const=min_const, max_const=max_const)

    unknown_text = f"상수를 몰라 묶지 못한 채보 {unknown}개"
    if not bands:
        lines = ["그 상수 범위에 플레이한 채보가 없어."]
        if unknown:
            lines.append(unknown_text)
        return ToolResult(
            data={"player_id": target.id, "min_const": min_const, "max_const": max_const, "unknown_const_count": unknown},
            display=Display(content="\n".join(lines)),
        )
    builder = TableBuilder(subject=target.id, unit="상수대")
    builder.column(_BAND, value_type="number")
    builder.column(_BAND_COUNT, value_type="number")
    builder.column(_BAND_AVERAGE, value_type="ScoreRef")
    builder.column(_BAND_RANKS)
    builder.row_formats([_ANALYZE_FORMAT])
    builder.step(" ".join(["상수대 분석", *_const_range_texts(min_const, max_const)]))
    if unknown:
        builder.summary({"상수를 몰라 묶지 못한 채보": f"{unknown}개"})
    for b in bands:
        builder.row(
            RowRef(_CONST_BAND, str(b.band)),
            {
                _BAND: cell(b.band),
                _BAND_COUNT: cell(b.chart_count),
                _BAND_AVERAGE: tc.score_cell(b.average_score),
                _BAND_RANKS: cell(", ".join(f"{rank} {n}" for rank, n in b.ranks.items())),
            },
        )
    return ToolResult(
        data={"player_id": target.id, "min_const": min_const, "max_const": max_const, "unknown_const_count": unknown},
        table=builder.build(),
    )


@tool(slash="후보곡")
async def candidates(
    ctx: ToolContext, min_const: float | None = None, max_const: float | None = None, count: int = 10
) -> ToolResult:
    """내 레이팅을 올릴 후보곡을 상승폭이 큰 순으로 표로 보여준다. SSS를 냈을 때 곡 레이팅이 내 프레임 3~8위 레이팅 사이가 되는 채보만 나온다. SSS까지 남은 점수를 붙인다. 상수 범위로 거를 수 있다."""
    hits = await ctx.records.candidates(ctx.player_id, min_const=min_const, max_const=max_const, count=max(1, count))
    if not hits:
        return ToolResult(
            data={"min_const": min_const, "max_const": max_const, "count": 0},
            display=Display(content="지금 조건으로는 레이팅을 올릴 후보곡이 없어."),
        )
    builder = TableBuilder(subject=ctx.player_id, unit="채보")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY, tc.CONST, tc.SCORE)
    builder.column(_REMAINING, value_type="number")
    builder.column(_UPSIDE, value_type="number")
    builder.row_formats(_CANDIDATE_FORMATS)
    builder.step(" ".join(["레이팅 후보곡 (상승폭 큰 순)", *_const_range_texts(min_const, max_const)]))
    unknown = sum(1 for h in hits if h.remaining_score is None)
    if unknown:
        # 노트 수를 몰라 남은 점수를 못 구한 채보는 그 칸이 비어 줄에서 빠진다. 빠졌다는 사실은 여기서 알린다.
        builder.summary({"SSS까지 남은 점수": f"{unknown}채보는 {_UNKNOWN}"})
    for h in hits:
        builder.row(
            tc.chart_ref(h.song_title, h.difficulty),
            {
                tc.SONG: tc.song_cell(h.song_title),
                tc.DIFFICULTY: tc.difficulty_cell(h.difficulty),
                tc.CONST: tc.const_cell(h.const),
                tc.SCORE: tc.score_cell(h.current_score),
                _REMAINING: (
                    cell(h.remaining_score, _score(h.remaining_score))
                    if h.remaining_score is not None else cell(None, _UNKNOWN)
                ),
                _UPSIDE: cell(float(h.upside), str(h.upside)),
            },
        )
    return ToolResult(
        data={"min_const": min_const, "max_const": max_const, "count": len(hits)},
        table=builder.build(),
    )


# ---------------------------------------------------------------- 동기화 리포트 조각


def _metric_lines(deltas: RatingDeltas | None) -> tuple[list[str], dict[str, Any]]:
    """레이팅·베스트30 평균·신곡20 평균의 (직전 →) 현재 (변화량) 줄과 data. 숫자 출처는
    /프로필·/베스트와 같은 records.rating_deltas(동기화 시점 스냅샷)다 — 같은 시점에 리포트와
    프로필이 다른 숫자를 보이지 않게 한다. 세션별로 나누지 않는다."""
    current: dict[str, Decimal | None] = {
        "computed_rating": deltas.computed_rating if deltas else None,
        "best30_average": deltas.best30_average if deltas else None,
        "new20_average": deltas.new20_average if deltas else None,
    }
    previous: dict[str, Decimal | None] | None = (
        {
            "computed_rating": deltas.previous_computed_rating,
            "best30_average": deltas.previous_best30_average,
            "new20_average": deltas.previous_new20_average,
        }
        if deltas is not None and deltas.has_previous
        else None
    )
    lines: list[str] = []
    for key, label in _METRIC_LABELS.items():
        if previous is None:
            lines.append(f"{label}  {_num(current[key])}")
        else:
            lines.append(
                f"{label}  {_num(previous[key])} → {_num(current[key])}  ({_delta(current[key], previous[key])})"
            )
    data: dict[str, Any] = {"current": {k: _num(v) for k, v in current.items()}}
    if previous is not None:
        data["previous"] = {k: _num(v) for k, v in previous.items()}
        data["change"] = {k: _delta(current[k], previous[k]) for k in current}
    return lines, data


def _update_line(ctx: ToolContext, u: ChartUpdate, *, session_estimated: bool) -> str:
    const = ctx.songdb.const_for(u.song_title, u.difficulty)
    head = f"{u.song_title} {u.difficulty}{_const_text(const)}"
    if u.old_score is None:
        score = f"신규 {_score(u.new_score)}"
        rank = u.new_rank
        rating = f"곡 레이팅 {_num(u.new_rating)}"
    else:
        score = f"{_score(u.old_score)} → {_score(u.new_score)}"
        rank = f"{u.new_rank} 유지" if u.old_rank == u.new_rank else f"{u.old_rank} → {u.new_rank}"
        rating = f"곡 레이팅 {_num(u.old_rating)} → {_num(u.new_rating)}"
    line = f"{head}  {score}  {rank}  {rating}"
    if u.entered_frame:
        line += "  프레임 진입"
    if u.estimated_session and not session_estimated:
        line += "  (날짜 추정)"
    return line


def _update_data(u: ChartUpdate) -> dict[str, Any]:
    return {
        "song_title": u.song_title, "difficulty": u.difficulty,
        "old_score": u.old_score, "new_score": u.new_score, "old_rank": u.old_rank, "new_rank": u.new_rank,
        "old_rating": None if u.old_rating is None else str(u.old_rating), "new_rating": _num(u.new_rating),
        "session_id": u.session_id, "estimated_session": u.estimated_session, "entered_frame": u.entered_frame,
    }


def _stage(diff: SyncDiff) -> Stage | None:
    """반응 단계 축소판(SKILL.md 본문 참고). rest와 "평소 점수 수준" 기준은 다음에 정한다."""
    if any(u.new_rank in _SSS_TIER for u in diff.chart_updates):
        return "great"
    if diff.chart_updates:
        return "good"
    if diff.unchanged_plays:
        return "close"
    return None


def _hint(diff: SyncDiff) -> str | None:
    sss = sum(1 for u in diff.chart_updates if u.new_rank in _SSS_TIER)
    entered = sum(1 for u in diff.chart_updates if u.entered_frame)
    parts = ([f"SSS 이상 갱신 {sss}개"] if sss else []) + ([f"프레임 진입 {entered}개"] if entered else [])
    return ", ".join(parts) or None


def _focus_keys(payload: dict[str, Any]) -> set[tuple[str, str]] | None:
    focus = payload.get("focus")
    if not focus:
        return None
    return {(item["song_title"], item["difficulty"]) for item in focus}


@on("chunithm.sync.completed")
async def on_sync_completed(ctx: ToolContext, payload: dict[str, Any]) -> Section:
    """동기화 리포트의 본문 조각(order 10): 세션 요약, 갱신 목록, 반응 단계."""
    player_id: str = payload["player_id"]
    deltas: RatingDeltas | None = await ctx.records.rating_deltas(player_id)
    if payload.get("is_first_sync"):
        # chunithm-sync는 첫 동기화에 이 이벤트를 내지 않는다. 오면 기준점만 알린다.
        return Section(
            order=_REPORT_ORDER,
            display=Display(content=f"기준점을 잡았어. 현재 레이팅 {_num(deltas.computed_rating if deltas else None)}"),
            data={"kind": "sync_report", "is_first_sync": True},
        )

    diff = await ctx.records.sync_diff(payload["sync_id"])
    if diff is None or not payload.get("has_new_plays"):
        # 변화 없음, 또는 비교 데이터가 캐시에서 밀려난 경우: 현재값(+있으면 변화량)만.
        metric_lines, metric_data = _metric_lines(deltas)
        heading = "변화 없음" if not payload.get("has_new_plays") else "현재 기록"
        return Section(
            order=_REPORT_ORDER,
            display=Display(content="\n".join([heading, *metric_lines])),
            data={"kind": "sync_report", "has_new_plays": bool(payload.get("has_new_plays")), **metric_data},
        )

    focus = _focus_keys(payload)
    shown_updates = [
        u for u in diff.chart_updates if focus is None or (u.song_title, u.difficulty) in focus
    ]
    sessions = list(diff.sessions)
    session_by_id = {s.id: s for s in sessions}

    def session_label(s_id: str) -> str:
        s = session_by_id[s_id]
        return _short_date(s.play_date) + (" (추정)" if s.estimated else "")

    metric_lines, metric_data = _metric_lines(deltas)
    dates = ", ".join(session_label(s.id) for s in sessions) or "날짜 미상"
    blocks: list[str] = [
        "\n".join([f"세션 요약  {dates}", *metric_lines, f"갱신 {len(diff.chart_updates)}채보"])
    ]

    # 갱신 목록은 날짜(세션)별로 나눈다. 세션에 없는 session_id(일어나면 안 됨)는 마지막에 모은다.
    lines_by_session: dict[str | None, list[str]] = {}
    for u in shown_updates:
        key = u.session_id if u.session_id in session_by_id else None
        estimated = session_by_id[u.session_id].estimated if key is not None else False
        lines_by_session.setdefault(key, []).append(_update_line(ctx, u, session_estimated=estimated))
    multi = len(sessions) > 1
    for s in sessions:
        lines = lines_by_session.get(s.id)
        if lines:
            blocks.append("\n".join([f"갱신  {session_label(s.id)}" if multi else "갱신", *lines]))
    if None in lines_by_session:
        blocks.append("\n".join(["갱신  날짜 미상", *lines_by_session[None]]))
    if focus is not None and not shown_updates:
        blocks.append("고른 채보는 이번에 갱신이 없어.")
    if diff.frame_exits:
        blocks.append(
            "\n".join(
                ["프레임 이탈"]
                + [f"{e.song_title} {e.difficulty}  곡 레이팅 {_num(e.old_rating)}" for e in diff.frame_exits]
            )
        )

    stage = _stage(diff)
    return Section(
        order=_REPORT_ORDER,
        display=Display(content="\n\n".join(blocks)),
        data={
            "kind": "sync_report",
            "has_new_plays": True,
            **metric_data,
            "sessions": [
                {
                    "id": s.id, "play_date": s.play_date, "estimated": s.estimated,
                    "updated_count": sum(1 for u in diff.chart_updates if u.session_id == s.id),
                    "unchanged_count": sum(1 for p in diff.unchanged_plays if p.session_id == s.id),
                }
                for s in sessions
            ],
            "focus": payload.get("focus"),
            "chart_updates": [_update_data(u) for u in shown_updates],
            "chart_updates_total": len(diff.chart_updates),
            "unchanged_plays": [
                {"song_title": p.song_title, "difficulty": p.difficulty, "play_count": p.play_count,
                 "current_score": p.current_score, "session_id": p.session_id}
                for p in diff.unchanged_plays
            ],
            "frame_exits": [
                {"song_title": e.song_title, "difficulty": e.difficulty, "old_rating": _num(e.old_rating)}
                for e in diff.frame_exits
            ],
            "stage": stage,
        },
        hint=_hint(diff),
        stage=stage,
    )
