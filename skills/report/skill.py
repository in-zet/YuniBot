# skills/report/skill.py
from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, NamedTuple

from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError
from chuni_penguin.types.score import RecentScore

from core.decorators import on, slash_only, tool
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
    DifficultyRef,
    Display,
    GameRef,
    PlayerRef,
    TableRef,
    ToolContext,
    ToolResult,
)
from services.common.players import Player, target_player
from services.game import table_columns as tc
from services.game.chart_ref import ResolvedChart
from services.game.profile import GameProfile
from services.game.rating_format import (
    rating_delta_text,
    rating_text,
    score_text,
    song_rating_text,
)
from services.game.records import (
    ChartUpdate,
    PbRow,
    RatingDeltas,
    SyncDiff,
)
from services.game.registry import profile_for
from services.game.songdb import ConstRangeHit
from services.game.terms import ResolvedDifficulty

# 판정 수(상세 판정)는 CHUNITHM-NET 플레이 상세 페이지를 한 곡씩 따로 열어야 붙는다.
# 설계서가 고정한 상한이다 — 바꾸지 않는다.
_DETAIL_LIMIT = 20
_UNKNOWN = "계산 불가"
_SSS_TIER = frozenset({"SSS", "SSS+"})
# analyze의 랭크 분포를 높은 랭크부터 보여 주는 순서.
_RANK_ORDER: tuple[str, ...] = (
    "SSS+", "SSS", "SS+", "SS", "S+", "S", "AAA", "AA", "A", "BBB", "BB", "B", "C", "D",
)
_NOT_SYNCED_ERROR = "아직 동기화한 기록이 없어. /동기화를 먼저 해 줘."
_REPORT_ORDER = 10


class BestKind(StrEnum):
    BEST = "best"
    NEW = "new"
    BOTH = "both"


# ---------------------------------------------------------------- 공통 도우미


async def _target_player(ctx: ToolContext, player: PlayerRef | None) -> Player | ToolResult:
    return await target_player(ctx, player)


def _profile(ctx: ToolContext) -> GameProfile:
    return profile_for(ctx.game)


def _song_rating(value: Decimal | None, profile: GameProfile) -> str:
    return song_rating_text(value, unknown=_UNKNOWN, places=profile.song_rating_places)


def _overall(value: Decimal | None, profile: GameProfile) -> str:
    """플레이어 전체 레이팅(CHUNITHM 소수 넷째 자리, maimai 정수)."""
    return rating_text(value, unknown=_UNKNOWN, places=profile.overall_places)


def _average(value: Decimal | None) -> str:
    """프레임 평균(곡 레이팅의 평균이라 두 게임 모두 소수 넷째 자리)."""
    return rating_text(value, unknown=_UNKNOWN)


def _delta(current: Decimal | None, previous: Decimal | None, *, places: int | None = None) -> str:
    if current is None or previous is None:
        return _UNKNOWN
    return rating_delta_text(current, previous) if places is None else rating_delta_text(current, previous, places=places)


def _score(value: int, profile: GameProfile) -> str:
    return score_text(value, percent=profile.score_is_percent)


def _const_text(const: float | None) -> str:
    return "" if const is None else f" {const}"


def _short_date(iso_date: str) -> str:
    """"2026-10-08" → "10/08"."""
    return f"{iso_date[5:7]}/{iso_date[8:10]}"


def _contribution(rating: Decimal | None, profile: GameProfile) -> str:
    # 전체 레이팅 = 프레임 곡 레이팅 합 / rating_divisor(CHUNITHM 50). 곡 레이팅이 둘째 자리 값이라 /50은
    # 넷째 자리에서 정확히 끝난다(반올림이 필요 없다). maimai는 나누지 않아 곡 레이팅 그대로다.
    return _UNKNOWN if rating is None else str(rating / profile.rating_divisor)


def _const_range_texts(min_const: float | None, max_const: float | None) -> list[str]:
    """표의 과정(steps)에 적는 상수 범위."""
    if min_const is None and max_const is None:
        return []
    low = "" if min_const is None else f"{min_const:.1f}"
    high = "" if max_const is None else f"{max_const:.1f}"
    return [f"상수 {low}~{high}"]


def _pb_data(row: PbRow, profile: GameProfile) -> dict[str, Any]:
    data: dict[str, Any] = {
        "song_title": row.song_title, "difficulty": row.difficulty, "score": row.score, "rank": row.rank,
        "clear_lamp": row.clear_lamp, "combo_lamp": row.combo_lamp, "chain_lamp": row.chain_lamp,
        "const": row.const, "rating": _song_rating(row.rating, profile),
    }
    if row.chart_type:
        data["chart_type"] = row.chart_type
    if row.dx_score is not None:
        data["dx_score"] = row.dx_score
    return data


def _metric_labels(profile: GameProfile) -> dict[str, str]:
    return {
        "computed_rating": "레이팅".ljust(8),
        "best30_average": profile.best_label.ljust(8),
        "new20_average": profile.new_label.ljust(8),
    }


def _metric_text(key: str, value: Decimal | None, profile: GameProfile) -> str:
    return _overall(value, profile) if key == "computed_rating" else _average(value)


# ---------------------------------------------------------------- 도구


@tool(slash="프로필")
async def profile(ctx: ToolContext, player: PlayerRef | None = None, game: GameRef | None = None) -> ToolResult:
    """닉네임, 레이팅·베스트 평균·신곡 평균(CHUNITHM은 베스트 30·신곡 20, maimai는 구곡 35·신곡 15)의 지금 값과 마지막 동기화 시각을 보여준다. player를 비우면 본인이다."""
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    summary = await ctx.records.latest_sync_summary(target.id)
    if summary is None:
        return ToolResult(error=_NOT_SYNCED_ERROR)
    game_profile = _profile(ctx)
    labels = _metric_labels(game_profile)

    # 직전 동기화 대비 변화량은 여기서 안 보여준다 — 동기화 리포트(세션 맥락) 전용이다
    # (사용자 확인 2026-10-18: 단순 조회에 변화량을 같이 보여줄 근거가 없었다).
    data: dict[str, Any] = {
        "player_id": target.id,
        "nickname": summary.nickname,
        "computed_rating": _overall(summary.computed_rating, game_profile),
        "best30_average": _average(summary.best30_average),
        "new20_average": _average(summary.new20_average),
        "last_synced_at": summary.last_synced_at,
    }
    lines = [
        f"{summary.nickname or '(닉네임 없음)'}",
        f"{labels['computed_rating']}  {data['computed_rating']}",
        f"{labels['best30_average']}  {data['best30_average']}",
        f"{labels['new20_average']}  {data['new20_average']}",
        f"마지막 동기화  {summary.last_synced_at}",
    ]
    return ToolResult(data=data, display=Display(content="\n".join(lines)), display_mode="optional")


_CONTRIBUTION = "기여"
_SCORE_TAIL = (tc.CONST, tc.SCORE, tc.RANK, tc.LAMP, tc.RATING)


def _best_columns(game: str) -> tuple[str, ...]:
    return (*tc.identity_columns(game), *_SCORE_TAIL)


def _best_row_format(game: str) -> str:
    # 한 줄 서식(램프는 글자 출력에서 숨긴다 — 줄 모양은 지금 베스트 줄 그대로).
    return f"{tc.identity_format(game)} {{상수}}  {{점수}} {{랭크}}  곡 레이팅 {{곡 레이팅}}"


def _pb_row_cells(row: PbRow, rank_scores: dict[str, int], game: str) -> dict[str, Cell]:
    profile = profile_for(game)
    cells = {
        **tc.identity_cells(row.song_title, row.chart_type, row.difficulty, game),
        tc.CONST: tc.const_cell(row.const),
        **tc.pb_cells(row, rank_scores, game=game),
    }
    cells[_CONTRIBUTION] = (
        cell(float(row.rating / profile.rating_divisor), _contribution(row.rating, profile))
        if row.rating is not None
        else cell(None, _UNKNOWN)
    )
    return cells


@tool(slash="베스트")
async def best(
    ctx: ToolContext, player: PlayerRef | None = None, kind: BestKind = BestKind.BOTH, game: GameRef | None = None
) -> ToolResult:
    """베스트 30과 신곡 20 표(maimai는 구곡 35와 신곡 15)에 채보, 점수, 곡 레이팅, 전체 레이팅 기여도를 담아 돌려준다. kind는 best(베스트·구곡), new(신곡), both(둘 다)."""
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
    game_profile = _profile(ctx)

    # 변화량은 여기서 안 보여준다 — profile()과 같은 이유(동기화 리포트 전용).
    frames: list[tuple[str, list[PbRow], Decimal | None]] = []
    if kind in (BestKind.BEST, BestKind.BOTH):
        frames.append((game_profile.best_label, best_rows, summary.best30_average))
    if kind in (BestKind.NEW, BestKind.BOTH):
        frames.append((game_profile.new_label, new_rows, summary.new20_average))

    builder = TableBuilder(subject=target.id, unit="채보")
    tc.add_columns(builder, *_best_columns(ctx.game), game=ctx.game)
    builder.column(_CONTRIBUTION, value_type="number", hidden_in_text=True)  # 물을 때만 `columns`로 보인다
    builder.row_formats([_best_row_format(ctx.game)])
    builder.step(" · ".join(title for title, _, _ in frames))
    header: dict[str, str] = {"닉네임": summary.nickname or "(닉네임 없음)"}
    if profile_row is not None and profile_row.level is not None:
        header["레벨"] = str(profile_row.level)
    header["레이팅"] = _overall(summary.computed_rating, game_profile)
    builder.summary(header)

    rank_scores = ctx.terms.rank_scores()
    for title, rows, average in frames:
        builder.group(title, {"평균": _average(average)})
        for r in rows:
            builder.row(tc.chart_ref(r.song_title, r.difficulty, r.chart_type), _pb_row_cells(r, rank_scores, ctx.game))
    return ToolResult(
        data={"player_id": target.id, "kind": kind.value, "computed_rating": _overall(summary.computed_rating, game_profile)},
        table=builder.build(),
    )


_SCORE_COLUMNS = (tc.SCORE, tc.RANK, tc.LAMP, tc.RATING)


async def _owner_label(ctx: ToolContext, target: Player) -> str:
    """점수 칸을 더한 표의 `steps`에 적는 "누구 점수"."""
    if target.id == ctx.player_id:
        return "내 점수"
    other = await ctx.records.latest_sync_summary(target.id)
    return f"{other.nickname} 점수" if other is not None and other.nickname else "상대 점수"


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

    key_of = _row_key
    pbs = await ctx.records.pb_rows_for(target.id, [key_of(row) for row in table.rows])
    rank_scores = ctx.terms.rank_scores()

    owner = await _owner_label(ctx, target)
    extended = extend_table(
        table,
        tc.common_columns(*_SCORE_COLUMNS, game=ctx.game),
        lambda row: tc.pb_cells(pbs.get(key_of(row)), rank_scores, game=ctx.game),
        steps=[owner],
    )
    return ToolResult(
        data={"player_id": target.id, "with_record": len(pbs)},
        table=replace(extended, subject=target.id),
    )


_CONST_TABLE_TAIL = (tc.CONST, tc.SCORE, tc.RANK, tc.LAMP, tc.RATING)


@tool()
async def const_table(
    ctx: ToolContext,
    min_const: float = 0,
    max_const: float | None = None,
    difficulty: DifficultyRef | None = None,
    only_played: bool = False,
    new_only: bool = False,
    player: PlayerRef | None = None,
    game: GameRef | None = None,
) -> ToolResult:
    """상수표: 상수 범위의 채보를 상수별로 묶고(큰 상수부터), 묶음 안은 난이도가 높은 순, 같은 난이도는 점수 높은 순으로
    늘어놓은 표에 점수·랭크·램프·곡 레이팅을 붙여 준다. 상수를 모르는 채보는 빠진다. 위쪽 한계를 말하지 않았으면 max_const를
    비운다("15 이상"). "15대"는 15.0~15.9다. 난이도를 말하지 않았으면 비워 모든 난이도를 보인다. 기록 없는 채보도 기본으로
    넣고(기록 없음), 사람이 "점수 있는 것만"이라고 했을 때만 only_played를 켠다. "신곡만"이면 new_only를 켠다 — 이때는
    min_const를 안 줘도 된다(신곡 범위 자체가 좁아 되묻지 않는다). 신곡이 아닌데 상수 범위를 안 말했으면 되묻는다(0부터
    전부로 지어내지 않는다). 누구 기록인지 말하지 않았으면 player를 비운다. 그림("상수표 그림으로")은 이 표를 table_image에
    넘긴다."""
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    code = None
    if difficulty is not None:
        assert isinstance(difficulty, ResolvedDifficulty)
        code = tc.LONG_TO_SHORT_DIFFICULTY.get(difficulty.canonical, difficulty.canonical)
    hits = ctx.songdb.find_songs(min_const=min_const, max_const=max_const, difficulty=code, new_only=new_only)
    pbs = await ctx.records.pb_rows_for(target.id, [_hit_key(hit) for hit in hits])
    if only_played:
        hits = [hit for hit in hits if _hit_key(hit) in pbs]
    if not hits:
        return ToolResult(
            data={"count": 0},
            display=Display(content="점수가 있는 채보가 없어." if only_played else "해당 범위의 채보가 없어."),
        )

    difficulty_rank = {name: index for index, name in enumerate(_profile(ctx).long_to_short)}

    def order(hit: ConstRangeHit) -> tuple[float, int, int, str, str]:
        assert hit.chart.const is not None  # min_const를 줬으니 상수를 아는 채보만 온다
        pb = pbs.get(_hit_key(hit))
        long_name = tc.SHORT_TO_LONG_DIFFICULTY.get(hit.chart.difficulty, hit.chart.difficulty)
        # 상수 → 난이도 → 점수가 모두 높은 쪽이 먼저. 기록 없는 채보는 점수 -1이라 같은 난이도 맨 뒤.
        return (
            -hit.chart.const, -difficulty_rank.get(long_name, -1), -(pb.score if pb else -1), hit.song.title,
            hit.chart.chart_type,
        )

    rank_scores = ctx.terms.rank_scores()
    identity = tc.identity_columns(ctx.game)
    builder = TableBuilder(subject=target.id, unit="채보")
    tc.add_columns(builder, *identity, *_CONST_TABLE_TAIL, game=ctx.game)
    builder.step(" ".join(part for part in (tc.const_range_text(min_const, max_const), code) if part))
    if new_only:
        builder.step("신곡만")
    builder.step(await _owner_label(ctx, target))
    if only_played:
        builder.step("점수 있는 것만")
    current: float | None = None
    for hit in sorted(hits, key=order):
        if hit.chart.const != current:
            current = hit.chart.const
            builder.group(f"{current:.1f}")
        builder.row(
            tc.chart_ref(hit.song.title, hit.chart.difficulty, hit.chart.chart_type),
            {
                **tc.chart_cells(hit.song, hit.chart, (*identity, tc.CONST)),
                **tc.pb_cells(pbs.get(_hit_key(hit)), rank_scores, game=ctx.game),
            },
        )
    return ToolResult(
        data={"player_id": target.id, "count": len(hits), "with_record": sum(1 for h in hits if _hit_key(h) in pbs)},
        table=builder.build(),
    )


def _hit_key(hit: ConstRangeHit) -> tuple[str, ...]:
    return tc.pb_key(hit.song.title, hit.chart.chart_type, hit.chart.difficulty)


def _row_key(row: Row) -> tuple[str, ...]:
    return tc.pb_key(*tc.row_chart_key(row))


_CONST_BAND = "report.const_band"
_BAND = "상수대"
_BAND_COUNT = "채보 수"
_BAND_AVERAGE = "평균 점수"
_BAND_RANKS = "랭크 분포"
_ANALYZE_FORMAT = "{상수대}대  {채보 수}채보  평균 {평균 점수}  ({랭크 분포})"
_UPSIDE = "상승폭"
_PLAY = "report.play"
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


@tool(slash="최근", cost="network", games=["chunithm"])
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
async def record(
    ctx: ToolContext, chart: ChartRef, player: PlayerRef | None = None, game: GameRef | None = None
) -> ToolResult:
    """채보 하나의 최고 기록(점수, 랭크, 램프, 곡 레이팅)을 보여준다. player를 비우면 본인이다."""
    assert isinstance(chart, ResolvedChart)
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    row = await ctx.records.pb_row(
        target.id, song_title=chart.song.title, difficulty=chart.chart.difficulty, chart_type=chart.chart.chart_type
    )
    chart_name = " ".join(part for part in (chart.song.title, chart.chart.chart_type, chart.chart.difficulty) if part)
    if row is None:
        return ToolResult(error=f"{chart_name}는 플레이한 기록이 없어.")

    game_profile = _profile(ctx)
    # 클리어 램프가 없는 게임(maimai)은 콤보·싱크 램프를 보인다.
    lamps = (row.clear_lamp, row.combo_lamp) if game_profile.clear_lamp_order else (row.combo_lamp, row.chain_lamp)
    head = " ".join(part for part in (row.song_title, row.chart_type, row.difficulty) if part)
    dx_line = "" if row.dx_score is None else f"\nDX 점수 {row.dx_score:,}"
    content = (
        f"{head}{_const_text(row.const)}\n"
        f"{_score(row.score, game_profile)} {row.rank}  {lamps[0]} / {lamps[1]}{dx_line}\n"
        f"곡 레이팅 {_song_rating(row.rating, game_profile)}"
    )
    return ToolResult(
        data={"player_id": target.id, **_pb_data(row, game_profile)},
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


@tool()
async def analyze(
    ctx: ToolContext,
    player: PlayerRef | None = None,
    min_const: float | None = None,
    max_const: float | None = None,
    table: Annotated[TableRef | None, TableNeeds(nonempty=[tc.SONG, tc.DIFFICULTY])] = None,
    game: GameRef | None = None,
) -> ToolResult:
    """플레이한 채보를 상수대(정수 단위)로 묶어 평균 점수와 랭크 분포를 보여준다. 상수 범위로 거를 수 있다.
    표를 주면 그 표의 채보만으로 통계를 낸다("이 폴더 랭크 분포"). 기록이 없는 채보는 세지 않는다."""
    assert table is None or isinstance(table, WorkTable)
    return await _analyze(ctx, player, min_const, max_const, table)


@slash_only(slash="분석")
async def analyze_slash(
    ctx: ToolContext,
    player: PlayerRef | None = None,
    min_const: float | None = None,
    max_const: float | None = None,
    game: GameRef | None = None,
) -> ToolResult:
    """플레이한 채보를 상수대(정수 단위)로 묶어 평균 점수와 랭크 분포를 보여준다. 상수 범위로 거를 수 있다."""
    return await _analyze(ctx, player, min_const, max_const, None)


async def _analyze(
    ctx: ToolContext, player: PlayerRef | None, min_const: float | None, max_const: float | None,
    table: WorkTable | None,
) -> ToolResult:
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    if table is None:
        rows = await ctx.records.all_pb_rows(target.id)
        if not rows:
            return ToolResult(error=_NOT_SYNCED_ERROR)
    else:
        found = await ctx.records.pb_rows_for(target.id, [_row_key(row) for row in table.rows])
        rows = list(found.values())
        if not rows:
            return ToolResult(error="그 표의 채보에 플레이 기록이 없어.")
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
    if table is not None:
        builder.step(f"표의 {table.row_count}채보 중 기록 있는 {len(rows)}채보만")
    if unknown:
        builder.summary({"상수를 몰라 묶지 못한 채보": f"{unknown}개"})
    for b in bands:
        builder.row(
            RowRef(_CONST_BAND, str(b.band)),
            {
                _BAND: cell(b.band),
                _BAND_COUNT: cell(b.chart_count),
                _BAND_AVERAGE: tc.score_cell(b.average_score, percent=_profile(ctx).score_is_percent),
                _BAND_RANKS: cell(", ".join(f"{rank} {n}" for rank, n in b.ranks.items())),
            },
        )
    return ToolResult(
        data={"player_id": target.id, "min_const": min_const, "max_const": max_const, "unknown_const_count": unknown},
        table=builder.build(),
    )


@tool(slash="후보곡")
async def candidates(
    ctx: ToolContext,
    min_const: float | None = None,
    max_const: float | None = None,
    count: int = 10,
    game: GameRef | None = None,
) -> ToolResult:
    """내 레이팅을 올릴 후보곡을 상승폭이 큰 순으로 표로 보여준다. 기준 점수(CHUNITHM SSS, maimai SSS+)를 냈을 때 곡 레이팅이 내 프레임 3~8위 레이팅 사이가 되는 채보만 나온다. CHUNITHM은 SSS까지 남은 점수를 붙인다. 상수 범위로 거를 수 있다."""
    hits = await ctx.records.candidates(ctx.player_id, min_const=min_const, max_const=max_const, count=max(1, count))
    if not hits:
        return ToolResult(
            data={"min_const": min_const, "max_const": max_const, "count": 0},
            display=Display(content="지금 조건으로는 레이팅을 올릴 후보곡이 없어."),
        )
    game_profile = _profile(ctx)
    # 달성률로 말하는 게임(maimai)은 "남은 점수"가 점수 차이가 아니라 노트 가중치에 달려 있어 이 칸을 두지 않는다.
    with_remaining = not game_profile.score_is_percent
    remaining_name = f"{game_profile.candidate_target_label}까지 남은 점수"
    head = tc.identity_format(ctx.game)
    formats = (
        (f"{head} {{상수}}  현재 {{점수}}  {game_profile.candidate_target_label}까지 {{{remaining_name}}}점  상승폭 +{{상승폭}}",)
        if with_remaining
        else ()
    ) + (f"{head} {{상수}}  현재 {{점수}}  상승폭 +{{상승폭}}",)
    builder = TableBuilder(subject=ctx.player_id, unit="채보")
    tc.add_columns(builder, *tc.identity_columns(ctx.game), tc.CONST, tc.SCORE, game=ctx.game)
    if with_remaining:
        builder.column(remaining_name, value_type="number")
    builder.column(_UPSIDE, value_type="number")
    builder.row_formats(formats)
    builder.step(" ".join(["레이팅 후보곡 (상승폭 큰 순)", *_const_range_texts(min_const, max_const)]))
    unknown = sum(1 for h in hits if h.remaining_score is None)
    if with_remaining and unknown:
        # 노트 수를 몰라 남은 점수를 못 구한 채보는 그 칸이 비어 줄에서 빠진다. 빠졌다는 사실은 여기서 알린다.
        builder.summary({remaining_name: f"{unknown}채보는 {_UNKNOWN}"})
    for h in hits:
        cells = {
            **tc.identity_cells(h.song_title, h.chart_type, h.difficulty, ctx.game),
            tc.CONST: tc.const_cell(h.const),
            tc.SCORE: tc.score_cell(h.current_score, percent=game_profile.score_is_percent),
            _UPSIDE: cell(float(h.upside), str(h.upside)),
        }
        if with_remaining:
            cells[remaining_name] = (
                cell(h.remaining_score, _score(h.remaining_score, game_profile))
                if h.remaining_score is not None else cell(None, _UNKNOWN)
            )
        builder.row(tc.chart_ref(h.song_title, h.difficulty, h.chart_type), cells)
    return ToolResult(
        data={"min_const": min_const, "max_const": max_const, "count": len(hits)},
        table=builder.build(),
    )


# ---------------------------------------------------------------- 동기화 리포트 조각


def _metric_lines(deltas: RatingDeltas | None, profile: GameProfile) -> tuple[list[str], dict[str, Any]]:
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

    def change(key: str) -> str:
        assert previous is not None
        return _delta(
            current[key], previous[key], places=profile.overall_places if key == "computed_rating" else None
        )

    for key, label in _metric_labels(profile).items():
        if previous is None:
            lines.append(f"{label}  {_metric_text(key, current[key], profile)}")
        else:
            lines.append(
                f"{label}  {_metric_text(key, previous[key], profile)} → {_metric_text(key, current[key], profile)}"
                f"  ({change(key)})"
            )
    data: dict[str, Any] = {"current": {k: _metric_text(k, v, profile) for k, v in current.items()}}
    if previous is not None:
        data["previous"] = {k: _metric_text(k, v, profile) for k, v in previous.items()}
        data["change"] = {k: change(k) for k in current}
    return lines, data


def _update_line(ctx: ToolContext, u: ChartUpdate, *, session_estimated: bool) -> str:
    profile = _profile(ctx)
    const = ctx.songdb.const_for(u.song_title, u.difficulty, u.chart_type)
    head = " ".join(part for part in (u.song_title, u.chart_type, u.difficulty) if part) + _const_text(const)
    if u.old_score is None:
        score = f"신규 {_score(u.new_score, profile)}"
        rank = u.new_rank
        rating = f"곡 레이팅 {_song_rating(u.new_rating, profile)}"
    else:
        score = f"{_score(u.old_score, profile)} → {_score(u.new_score, profile)}"
        rank = f"{u.new_rank} 유지" if u.old_rank == u.new_rank else f"{u.old_rank} → {u.new_rank}"
        rating = f"곡 레이팅 {_song_rating(u.old_rating, profile)} → {_song_rating(u.new_rating, profile)}"
    line = f"{head}  {score}  {rank}  {rating}"
    if u.entered_frame:
        line += "  프레임 진입"
    if u.estimated_session and not session_estimated:
        line += "  (날짜 추정)"
    return line


def _update_data(u: ChartUpdate, profile: GameProfile) -> dict[str, Any]:
    data: dict[str, Any] = {
        "song_title": u.song_title, "difficulty": u.difficulty,
        "old_score": u.old_score, "new_score": u.new_score, "old_rank": u.old_rank, "new_rank": u.new_rank,
        "old_rating": None if u.old_rating is None else _song_rating(u.old_rating, profile),
        "new_rating": _song_rating(u.new_rating, profile),
        "session_id": u.session_id, "estimated_session": u.estimated_session, "entered_frame": u.entered_frame,
    }
    if u.chart_type:
        data["chart_type"] = u.chart_type
    return data


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


def _chart_name(item: Any) -> str:
    """"곡 (종류) 난이도". 채보 종류가 없는 게임은 종류가 빠진다."""
    return " ".join(part for part in (item.song_title, item.chart_type, item.difficulty) if part)


def _focus_keys(payload: dict[str, Any]) -> set[tuple[str, str]] | None:
    focus = payload.get("focus")
    if not focus:
        return None
    return {(item["song_title"], item["difficulty"]) for item in focus}


@on("chunithm.sync.completed")
async def on_sync_completed(ctx: ToolContext, payload: dict[str, Any]) -> Section:
    """동기화 리포트의 본문 조각(order 10): 세션 요약, 갱신 목록, 반응 단계."""
    return await _sync_section(ctx, payload)


@on("maimai.sync.completed")
async def on_maimai_sync_completed(ctx: ToolContext, payload: dict[str, Any]) -> Section:
    """`on_sync_completed`의 maimai 판. 이벤트 버스가 핸들러의 `records`·`songdb`를 maimai 것으로 바꿔 준다."""
    return await _sync_section(ctx, payload)


async def _sync_section(ctx: ToolContext, payload: dict[str, Any]) -> Section:
    profile = _profile(ctx)
    player_id: str = payload["player_id"]
    deltas: RatingDeltas | None = await ctx.records.rating_deltas(player_id)
    if payload.get("is_first_sync"):
        # sync는 첫 동기화에 이 이벤트를 내지 않는다. 오면 기준점만 알린다.
        return Section(
            order=_REPORT_ORDER,
            display=Display(content=f"기준점을 잡았어. 현재 레이팅 {_overall(deltas.computed_rating if deltas else None, profile)}"),
            data={"kind": "sync_report", "is_first_sync": True},
        )

    diff = await ctx.records.sync_diff(payload["sync_id"])
    if diff is None or not payload.get("has_new_plays"):
        # 변화 없음, 또는 비교 데이터가 캐시에서 밀려난 경우: 현재값(+있으면 변화량)만.
        metric_lines, metric_data = _metric_lines(deltas, profile)
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

    metric_lines, metric_data = _metric_lines(deltas, profile)
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
                + [f"{_chart_name(e)}  곡 레이팅 {_song_rating(e.old_rating, profile)}" for e in diff.frame_exits]
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
            "chart_updates": [_update_data(u, profile) for u in shown_updates],
            "chart_updates_total": len(diff.chart_updates),
            "unchanged_plays": [
                {"song_title": p.song_title, "difficulty": p.difficulty, "play_count": p.play_count,
                 "current_score": p.current_score, "session_id": p.session_id}
                for p in diff.unchanged_plays
            ],
            "frame_exits": [
                {
                    "song_title": e.song_title, "difficulty": e.difficulty,
                    **({"chart_type": e.chart_type} if e.chart_type else {}),
                    "old_rating": _song_rating(e.old_rating, profile),
                }
                for e in diff.frame_exits
            ],
            "stage": stage,
        },
        hint=_hint(diff),
        stage=stage,
    )
