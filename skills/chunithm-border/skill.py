# skills/chunithm-border/skill.py
from __future__ import annotations

from core.decorators import tool
from core.tool_types import ChartRef, Display, RankRef, ToolContext, ToolResult
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.terms import ResolvedRank

# chunithm-border.md "계산" 표가 명시한 기준 점수 — terms 서비스를 또 부르지 않고
# 설계서가 고정값으로 제시한 숫자를 그대로 쓴다(결정 사항 8번).
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
async def remaining(ctx: ToolContext, chart: ChartRef, rank: RankRef) -> ToolResult:
    """내 최고 점수에서 목표 랭크까지 남은 점수와 어택을 계산한다."""
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

    data = {
        "song": chart.song.title, "difficulty": chart.chart.difficulty, "rank": rank.canonical,
        "best_score": best_score, "remaining_score": remaining_score,
        "remaining_attacks": str(attacks), "already_achieved": already_achieved,
    }
    if already_achieved:
        content = f"{chart.song.title} {chart.chart.difficulty} — 이미 {rank.canonical}를 달성했다."
    else:
        content = f"{chart.song.title} {chart.chart.difficulty} — {rank.canonical}까지 {remaining_score}점 ({attacks}어택)."

    return ToolResult(data=data, display=Display(content=content), display_mode="optional")
