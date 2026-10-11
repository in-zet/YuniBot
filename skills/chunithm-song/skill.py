# skills/chunithm-song/skill.py
from __future__ import annotations

from core.decorators import tool
from core.tool_types import (
    ChartRef,
    DifficultyRef,
    Display,
    SongQuery,
    ToolContext,
    ToolResult,
)
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.songdb import LONG_TO_SHORT_DIFFICULTY, SongRecord
from services.chunithm.terms import ResolvedDifficulty

_DISPLAY_ORDER = ("BAS", "ADV", "EXP", "MAS", "ULT", "WE")
_MAX_LISTED = 25


@tool(slash="곡")
async def info(ctx: ToolContext, song: SongQuery) -> ToolResult:
    """곡 정보와 난이도별 채보(상수, 노트 수)를 보여준다."""
    assert isinstance(song, SongRecord)
    lines = [f"{song.title} — {song.artist}".rstrip(" —")]
    if song.bpm is not None:
        lines.append(f"BPM {song.bpm}")
    if song.version is not None:
        lines.append(f"수록 버전: {song.version}")
    for code in _DISPLAY_ORDER:
        chart = song.charts.get(code)
        if chart is None or not chart.is_available:
            continue
        const_suffix = "(추정)" if chart.is_const_unknown else ""
        note_text = f"{chart.note_count}노트" if chart.note_count is not None else "노트 수 알 수 없음"
        lines.append(f"{code} Lv.{chart.level or '?'} 상수 {chart.const}{const_suffix} — {note_text}")

    await ctx.event_bus.emit_gather(
        "chunithm.song.viewed", {"player_id": ctx.player_id, "song_title": song.title, "difficulty": None}
    )

    return ToolResult(
        data={"title": song.title, "artist": song.artist, "is_new": song.is_new},
        display=Display(content="\n".join(lines)),
    )


@tool()
async def chart(ctx: ToolContext, chart: ChartRef) -> ToolResult:
    """채보 하나의 상수와 노트 수를 돌려준다."""
    assert isinstance(chart, ResolvedChart)
    note_text = f"{chart.chart.note_count}노트" if chart.chart.note_count is not None else "계산 불가"
    return ToolResult(
        data={
            "song": chart.song.title, "difficulty": chart.chart.difficulty,
            "const": chart.chart.const, "note_count": chart.chart.note_count,
            "is_const_unknown": chart.chart.is_const_unknown,
        },
        display=Display(content=f"{chart.song.title} {chart.chart.difficulty} 상수 {chart.chart.const} ({note_text})"),
        display_mode="optional",
    )


@tool(slash="상수")
async def find_by_const(
    ctx: ToolContext, min_const: float, max_const: float, difficulty: DifficultyRef
) -> ToolResult:
    """상수 범위와 난이도로 채보 목록을 상수순으로 보여준다."""
    assert isinstance(difficulty, ResolvedDifficulty)
    short_code = LONG_TO_SHORT_DIFFICULTY.get(difficulty.canonical, difficulty.canonical)
    hits = ctx.songdb.find_by_const(min_const=min_const, max_const=max_const, difficulty=short_code)

    lines = [f"{hit.song.title} — 상수 {hit.chart.const}" for hit in hits[:_MAX_LISTED]]
    if len(hits) > _MAX_LISTED:
        lines.append(f"...외 {len(hits) - _MAX_LISTED}개")

    return ToolResult(
        data={"count": len(hits), "titles": [hit.song.title for hit in hits[:_MAX_LISTED]]},
        display=Display(content="\n".join(lines) if lines else "해당 범위의 채보가 없다."),
    )


@tool()
async def chart_link(ctx: ToolContext, chart: ChartRef) -> ToolResult:
    """채보 뷰어 링크(sdvx.in, 없으면 유튜브 검색)를 돌려준다."""
    assert isinstance(chart, ResolvedChart)
    link = ctx.songdb.chart_link(chart.song, chart.chart)
    return ToolResult(
        data={"url": link.url, "has_mapping": link.has_mapping},
        display=Display(content=link.url),
        display_mode="optional",
    )


@tool(slash="별칭 추가", mutates=True)
async def alias_add(ctx: ToolContext, song: SongQuery, alias: str) -> ToolResult:
    """한국어 별칭을 이 서버에 등록한다."""
    assert isinstance(song, SongRecord)
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 별칭을 추가할 수 있다")
    try:
        await ctx.songdb.add_alias(guild_id=ctx.guild_id, alias=alias, song_title=song.title, added_by=ctx.player_id)
    except ValueError as exc:
        return ToolResult(error=str(exc))
    return ToolResult(
        data={"alias": alias, "song": song.title},
        display=Display(content=f"'{alias}' → {song.title}로 등록했다."),
        display_mode="optional",
    )


@tool(slash="별칭 삭제", mutates=True)
async def alias_remove(ctx: ToolContext, alias: str) -> ToolResult:
    """별칭을 지운다. 추가한 사람이나 admin만 할 수 있다."""
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 별칭을 지울 수 있다")
    row = await ctx.songdb.get_alias(guild_id=ctx.guild_id, alias=alias)
    if row is None:
        return ToolResult(error="그런 별칭이 없다")
    if row.added_by != ctx.player_id and "admin" not in ctx.tags:
        return ToolResult(error="추가한 사람이나 admin만 지울 수 있다")
    await ctx.songdb.remove_alias(guild_id=ctx.guild_id, alias=alias)
    return ToolResult(data={"alias": alias}, display=Display(content=f"'{alias}' 별칭을 지웠다."), display_mode="optional")
