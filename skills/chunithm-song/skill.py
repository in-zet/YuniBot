# skills/chunithm-song/skill.py
from __future__ import annotations

from core.decorators import tool
from core.events import Section
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


def _difficulty_code(difficulty: DifficultyRef | None) -> str | None:
    """난이도 인자를 songdb 조회용 짧은 코드로 바꾼다. 비어 있으면 난이도를 가리지 않는다는
    뜻으로 None을 그대로 돌려준다(find_by_version의 "비우면 버전 전체" 규칙과 같다)."""
    if difficulty is None:
        return None
    assert isinstance(difficulty, ResolvedDifficulty)
    return LONG_TO_SHORT_DIFFICULTY.get(difficulty.canonical, difficulty.canonical)


def _with_sections(head: str, sections: list[Section]) -> str:
    """곡 조회 조각(chunithm.song.viewed 모으기 결과, order순)을 본문 뒤에 빈 줄로 나눠 붙인다.
    chunithm-sync가 동기화 리포트 조각을 붙이는 것과 같은 방식이다."""
    parts = [head, *(s.display.content for s in sections if s.display is not None and s.display.content)]
    return "\n\n".join(parts)


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

    # difficulty None = 곡 전체 보기. 받는 스킬은 그 곡의 모든 난이도에 걸친 것을 보여준다.
    sections = await ctx.emit_gather(
        "chunithm.song.viewed", {"player_id": ctx.player_id, "song_title": song.title, "difficulty": None}
    )

    return ToolResult(
        data={"title": song.title, "artist": song.artist, "is_new": song.is_new},
        display=Display(content=_with_sections("\n".join(lines), sections)),
    )


@tool()
async def chart(ctx: ToolContext, chart: ChartRef) -> ToolResult:
    """채보 하나의 상수와 노트 수를 돌려준다(내 To-Do·메모가 있으면 함께 보여준다)."""
    assert isinstance(chart, ResolvedChart)
    note_text = f"{chart.chart.note_count}노트" if chart.chart.note_count is not None else "계산 불가"
    sections = await ctx.emit_gather(
        "chunithm.song.viewed",
        {"player_id": ctx.player_id, "song_title": chart.song.title, "difficulty": chart.chart.difficulty},
    )
    head = f"{chart.song.title} {chart.chart.difficulty} 상수 {chart.chart.const} ({note_text})"
    return ToolResult(
        data={
            "song": chart.song.title, "difficulty": chart.chart.difficulty,
            "const": chart.chart.const, "note_count": chart.chart.note_count,
            "is_const_unknown": chart.chart.is_const_unknown,
        },
        display=Display(content=_with_sections(head, sections)),
        display_mode="optional",
    )


@tool(slash="상수")
async def find_by_const(
    ctx: ToolContext, min_const: float, difficulty: DifficultyRef,
    max_const: float | None = None, new_only: bool = False,
) -> ToolResult:
    """상수 범위와 난이도로 채보 목록을 상수순으로 보여준다. "14.7 이상"처럼 위쪽 한계를 말하지 않았으면
    max_const를 비운다(지어내지 않는다). 플레이어가 신곡만 말했으면 new_only를 켠다."""
    short_code = _difficulty_code(difficulty)
    hits = ctx.songdb.find_by_const(
        min_const=min_const, max_const=max_const, difficulty=short_code, new_only=new_only
    )

    lines = [f"{hit.song.title} — 상수 {hit.chart.const}" for hit in hits]

    return ToolResult(
        data={"count": len(hits), "titles": [hit.song.title for hit in hits]},
        display=Display(content="\n".join(lines) if lines else "해당 범위의 채보가 없다.", omittable=True),
    )


@tool(slash="버전")
async def find_by_version(
    ctx: ToolContext, version: str, difficulty: DifficultyRef | None = None
) -> ToolResult:
    """수록 버전으로 채보 목록을 상수순으로 보여준다. 난이도를 안 주면 그 버전의 모든 채보를
    난이도 가리지 않고 보여준다."""
    short_code = _difficulty_code(difficulty)
    hits = ctx.songdb.find_by_version(version=version, difficulty=short_code)

    lines = [f"{hit.song.title} {hit.chart.difficulty} — 상수 {hit.chart.const}" for hit in hits]

    return ToolResult(
        data={"count": len(hits), "titles": [hit.song.title for hit in hits]},
        display=Display(content="\n".join(lines) if lines else "해당 버전의 채보가 없다.", omittable=True),
    )


@tool(slash="작곡가")
async def find_by_artist(ctx: ToolContext, artist: str) -> ToolResult:
    """작곡가(아티스트) 이름으로 국제판 수록곡을 찾는다."""
    songs = ctx.songdb.find_by_artist(name=artist)

    lines = [f"{song.title} — {song.artist}" for song in songs]

    return ToolResult(
        data={"count": len(songs), "titles": [song.title for song in songs]},
        display=Display(content="\n".join(lines) if lines else "해당 작곡가의 곡이 없다.", omittable=True),
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
