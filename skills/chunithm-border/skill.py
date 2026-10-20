# skills/chunithm-border/skill.py
from __future__ import annotations

from typing import Annotated

from core.decorators import tool
from core.tables import Cell, Column, Row, TableNeeds, WorkTable, cell, extend_table
from core.tool_types import (
    ChartRef,
    Display,
    RankRef,
    TableRef,
    ToolContext,
    ToolResult,
)
from services.chunithm import table_columns as tc
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.songdb import LONG_TO_SHORT_DIFFICULTY
from services.chunithm.terms import ResolvedRank

# chunithm-border.md "계산" 표가 명시한 기준 점수 — terms 서비스를 또 부르지 않고
# 설계서가 고정값으로 제시한 숫자를 그대로 쓴다(chunithm-border.md "구현 결정").
_DEFAULT_BORDER_RANKS: tuple[tuple[str, int], ...] = (
    ("SSS+", 1_009_000), ("SSS", 1_007_500), ("SS+", 1_005_000), ("SS", 1_000_000),
)


@tool(slash="보더")
async def lookup(ctx: ToolContext, chart: ChartRef, rank: RankRef | None = None) -> ToolResult:
    """곡과 난이도의 랭크별 허용 어택 개수를 계산한다. 랭크를 안 주면 SSS+/SSS/SS+/SS 네 개를 모두 보여준다."""
    assert isinstance(chart, ResolvedChart)
    assert rank is None or isinstance(rank, ResolvedRank)
    targets = [(rank.canonical, rank.min_score)] if rank is not None else list(_DEFAULT_BORDER_RANKS)

    borders: dict[str, str] = {}
    for name, min_score in targets:
        attacks = ctx.records.allowed_attacks(chart.chart.note_count, min_score)
        borders[name] = "계산 불가" if attacks is None else str(attacks)

    display_lines = [f"{chart.song.title} {chart.chart.difficulty} 보더"]
    display_lines += [f"{name}: {value}어택" for name, value in borders.items()]

    return ToolResult(
        data={"song": chart.song.title, "difficulty": chart.chart.difficulty, "borders": borders},
        display=Display(content="\n".join(display_lines)),
        display_mode="optional",
    )


@tool(slash="남은어택", tags=["chunithm-synced"])
async def remaining(
    ctx: ToolContext, chart: ChartRef, rank: RankRef, include_attacks: bool = False
) -> ToolResult:
    """내 최고 점수에서 목표 랭크까지 남은 점수를 계산한다. 플레이어가 질문에 "어택"을 명시했을 때만
    include_attacks를 켜서 남은 어택도 함께 낸다."""
    assert isinstance(chart, ResolvedChart)
    assert isinstance(rank, ResolvedRank)
    best_score = await ctx.records.best_score(
        ctx.player_id, song_title=chart.song.title, difficulty=chart.chart.difficulty
    )
    if best_score is None:
        return ToolResult(error="그 채보를 아직 플레이한 기록이 없다")

    result = ctx.records.remaining_to_target(chart.chart.note_count, best_score, rank.min_score)
    if result is None:
        return ToolResult(error="노트 수를 몰라 계산 불가다")

    remaining_score, attacks = result
    already_achieved = remaining_score <= 0

    data: dict[str, object] = {
        "song": chart.song.title, "difficulty": chart.chart.difficulty, "rank": rank.canonical,
        "best_score": best_score, "remaining_score": remaining_score, "already_achieved": already_achieved,
    }
    if include_attacks:
        data["remaining_attacks"] = str(attacks)
    head = f"{chart.song.title} {chart.chart.difficulty} — "
    if already_achieved:
        content = f"{head}이미 {rank.canonical}를 달성했다."
        slash_content = None
    else:
        content = f"{head}{rank.canonical}까지 {remaining_score}점" + (f" ({attacks}어택)." if include_attacks else ".")
        # /남은어택은 이름부터 어택을 명시한 명령이라 슬래시에서는 어택을 같이 보여 준다.
        slash_content = f"{head}{rank.canonical}까지 {remaining_score}점 ({attacks}어택)."

    return ToolResult(
        data=data, display=Display(content=content, slash_content=slash_content), display_mode="optional"
    )


_CHART_NEEDS = TableNeeds(nonempty=[tc.SONG, tc.DIFFICULTY])


def _chart_key(row: Row) -> tuple[str, str]:
    difficulty = str(row.cell(tc.DIFFICULTY).raw)
    return str(row.cell(tc.SONG).raw), LONG_TO_SHORT_DIFFICULTY.get(difficulty, difficulty)


def _note_count(ctx: ToolContext, key: tuple[str, str]) -> int | None:
    """표 행의 노트 수는 칸을 믿지 않고 곡 데이터에서 다시 읽는다(lookup·remaining과 같은 출처)."""
    song = ctx.songdb.song_by_title(key[0])
    chart = None if song is None else song.charts.get(key[1])
    return None if chart is None else chart.note_count


@tool()
async def add_borders(
    ctx: ToolContext, table: Annotated[TableRef, _CHART_NEEDS], rank: RankRef
) -> ToolResult:
    """채보 표에 "<랭크> 허용 어택" 칸을 더한 새 표를 돌려준다. 저스티스 0·미스 0 기준 허용 어택이고,
    노트 수를 모르는 채보는 "계산 불가"다."""
    assert isinstance(table, WorkTable)
    assert isinstance(rank, ResolvedRank)
    name = f"{rank.canonical} 허용 어택"

    def cells_for(row: Row) -> dict[str, Cell]:
        attacks = ctx.records.allowed_attacks(_note_count(ctx, _chart_key(row)), rank.min_score)
        return {name: cell(None, "계산 불가") if attacks is None else cell(float(attacks), str(attacks))}

    extended = extend_table(
        table, [Column(name=name, label=name, value_type="number")], cells_for,
        steps=[f"{rank.canonical} 보더"],
    )
    unknown = sum(1 for row in extended.rows if row.cell(name).raw is None)
    return ToolResult(data={"rank": rank.canonical, "count": extended.row_count, "unknown": unknown}, table=extended)


@tool(tags=["chunithm-synced"])
async def add_remaining(
    ctx: ToolContext, table: Annotated[TableRef, _CHART_NEEDS], rank: RankRef
) -> ToolResult:
    """채보 표에 내 최고 점수에서 "<랭크>까지 남은 점수" 칸을 더한 새 표를 돌려준다. 이미 넘었으면 "달성",
    기록이 없거나 노트 수를 모르면 값이 없다("기록 없음"·"계산 불가"). 어택은 더하지 않는다."""
    assert isinstance(table, WorkTable)
    assert isinstance(rank, ResolvedRank)
    name = f"{rank.canonical}까지 남은 점수"
    pbs = await ctx.records.pb_rows_for(ctx.player_id, [_chart_key(row) for row in table.rows])

    def cells_for(row: Row) -> dict[str, Cell]:
        key = _chart_key(row)
        pb = pbs.get(key)
        if pb is None:
            return {name: cell(None, tc.NO_RECORD)}
        result = ctx.records.remaining_to_target(_note_count(ctx, key), pb.score, rank.min_score)
        if result is None:
            return {name: cell(None, "계산 불가")}
        remaining_score = max(result[0], 0)
        return {name: cell(remaining_score, "달성" if remaining_score == 0 else f"{remaining_score}점")}

    extended = extend_table(
        table, [Column(name=name, label=name, value_type="number")], cells_for,
        steps=[f"내 점수 기준 {rank.canonical}까지"],
    )
    return ToolResult(
        data={"rank": rank.canonical, "count": extended.row_count, "with_record": len(pbs)}, table=extended
    )
