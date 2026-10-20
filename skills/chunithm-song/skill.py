# skills/chunithm-song/skill.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Annotated

from core.decorators import tool
from core.events import Section
from core.tables import (
    Cell,
    Column,
    Row,
    TableBuilder,
    TableNeeds,
    WorkTable,
    cell,
    expand_rows,
    extend_table,
)
from core.tool_types import (
    ChartRef,
    DifficultyRef,
    Display,
    SongQuery,
    TableRef,
    ToolContext,
    ToolResult,
)
from services.chunithm import table_columns as tc
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.songdb import LONG_TO_SHORT_DIFFICULTY, ConstRangeHit, SongRecord
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


_SEARCH_COLUMNS = (tc.SONG, tc.DIFFICULTY, tc.LEVEL, tc.CONST, tc.NOTE_COUNT, tc.VERSION)
_NO_ARTIST_SONGS = "해당 작곡가의 곡이 없다."


def _num_text(value: float) -> str:
    return f"{value:.1f}" if round(value, 1) == value else f"{value:g}"


def _const_range_text(min_const: float | None, max_const: float | None) -> str | None:
    if min_const is None and max_const is None:
        return None
    if max_const is None and min_const is not None:
        return f"{_num_text(min_const)} 이상 상수"
    if min_const is None and max_const is not None:
        return f"{_num_text(max_const)} 이하 상수"
    assert min_const is not None and max_const is not None
    return f"{_num_text(min_const)}~{_num_text(max_const)} 상수"


def _note_range_text(min_notes: int | None, max_notes: int | None) -> str | None:
    if min_notes is None and max_notes is None:
        return None
    if max_notes is None:
        return f"노트 {min_notes}개 이상"
    if min_notes is None:
        return f"노트 {max_notes}개 이하"
    return f"노트 {min_notes}~{max_notes}개"


def _chart_table(hits: list[ConstRangeHit], conditions: list[str | None]) -> WorkTable:
    """채보 검색 결과를 표로. 순서는 서비스가 정한 그대로(상수 오름차순)다."""
    builder = TableBuilder(unit="채보")
    tc.add_columns(builder, *_SEARCH_COLUMNS)
    builder.step(" ".join(c for c in conditions if c))
    for hit in hits:
        builder.row(
            tc.chart_ref(hit.song.title, hit.chart.difficulty),
            tc.chart_cells(hit.song, hit.chart, _SEARCH_COLUMNS),
        )
    return builder.build()


@tool(slash="상수")
async def find_by_const(
    ctx: ToolContext, min_const: float, difficulty: DifficultyRef,
    max_const: float | None = None, new_only: bool = False,
) -> ToolResult:
    """상수 범위와 난이도로 채보 목록을 상수순으로 찾는다. "14.7 이상"처럼 위쪽 한계를 말하지 않았으면
    max_const를 비운다(지어내지 않는다). 플레이어가 신곡만 말했으면 new_only를 켠다."""
    short_code = _difficulty_code(difficulty)
    hits = ctx.songdb.find_by_const(
        min_const=min_const, max_const=max_const, difficulty=short_code, new_only=new_only
    )
    if not hits:
        return ToolResult(data={"count": 0}, display=Display(content="해당 범위의 채보가 없다."))
    conditions = [_const_range_text(min_const, max_const), short_code, "신곡만" if new_only else None]
    return ToolResult(data={"count": len(hits)}, table=_chart_table(hits, conditions))


@tool(slash="버전")
async def find_by_version(
    ctx: ToolContext, version: str, difficulty: DifficultyRef | None = None
) -> ToolResult:
    """수록 버전으로 채보 목록을 상수순으로 찾는다. 난이도를 안 주면 그 버전의 모든 채보를
    난이도 가리지 않고 찾는다."""
    short_code = _difficulty_code(difficulty)
    hits = ctx.songdb.find_by_version(version=version, difficulty=short_code)
    if not hits:
        return ToolResult(data={"count": 0}, display=Display(content="해당 버전의 채보가 없다."))
    return ToolResult(data={"count": len(hits)}, table=_chart_table(hits, [f"{version} 버전", short_code]))


@tool(slash="검색")
async def find_songs(
    ctx: ToolContext,
    min_const: float | None = None,
    max_const: float | None = None,
    level: str | None = None,
    version: str | None = None,
    difficulty: DifficultyRef | None = None,
    min_note_count: int | None = None,
    max_note_count: int | None = None,
    new_only: bool = False,
) -> ToolResult:
    """상수 범위·레벨·수록 버전·난이도·노트 수 범위·신곡 여부를 한 번에 조합해 채보를 찾는다.
    `/상수`·`/버전`은 조건 하나(+난이도)만 받으니, 조건을 두 개 이상 동시에 걸 때만 이걸 쓴다.
    조건을 하나도 안 줬으면 범위가 너무 넓으니 바로 에러를 돌려주고, 조건을 더 물어본다.
    결과는 상수 오름차순 표다. 다른 순서가 필요하면 표에 `table_query`를 쓴다."""
    if not new_only and all(
        v is None
        for v in (min_const, max_const, level, version, difficulty, min_note_count, max_note_count)
    ):
        return ToolResult(error="조건을 하나는 줘야 한다(상수 범위, 레벨, 버전, 난이도, 노트 수, 신곡 중)")

    short_code = _difficulty_code(difficulty)
    hits = ctx.songdb.find_songs(
        min_const=min_const, max_const=max_const, level=level, version=version,
        difficulty=short_code, min_note_count=min_note_count, max_note_count=max_note_count,
        new_only=new_only,
    )
    if not hits:
        return ToolResult(data={"count": 0}, display=Display(content="조건에 맞는 채보가 없다."))
    conditions = [
        _const_range_text(min_const, max_const),
        f"레벨 {level}" if level else None,
        f"{version} 버전" if version else None,
        short_code,
        _note_range_text(min_note_count, max_note_count),
        "신곡만" if new_only else None,
    ]
    return ToolResult(data={"count": len(hits)}, table=_chart_table(hits, conditions))


@tool(slash="작곡가")
async def find_by_artist(
    ctx: ToolContext, artist: str, difficulty: DifficultyRef | None = None
) -> ToolResult:
    """작곡가(아티스트) 이름으로 국제판 수록곡을 찾는다. 사용자가 난이도를 말했을 때만 difficulty를 주고,
    말하지 않았으면 비운다(곡 단위 행으로 나온다)."""
    songs = ctx.songdb.find_by_artist(name=artist)
    short_code = _difficulty_code(difficulty)
    condition = f"{artist} 작곡"

    if short_code is None:
        columns = (tc.SONG, tc.DIFFICULTY, tc.ARTIST, tc.VERSION)
        if not songs:
            return ToolResult(data={"count": 0}, display=Display(content=_NO_ARTIST_SONGS))
        builder = TableBuilder(unit="곡")
        tc.add_columns(builder, *columns).step(condition)
        for song in songs:
            builder.row(tc.song_ref(song.title), tc.song_cells(song, columns))
        return ToolResult(data={"count": len(songs)}, table=builder.build())

    columns = (tc.SONG, tc.DIFFICULTY, tc.LEVEL, tc.CONST, tc.ARTIST, tc.VERSION)
    charts = [
        (song, song.charts[short_code])
        for song in songs
        if short_code in song.charts and song.charts[short_code].is_available
    ]
    if not charts:
        return ToolResult(data={"count": 0}, display=Display(content=_NO_ARTIST_SONGS))
    builder = TableBuilder(unit="채보")
    tc.add_columns(builder, *columns).step(f"{condition} {short_code}")
    for song, chart_record in charts:
        builder.row(tc.chart_ref(song.title, short_code), tc.chart_cells(song, chart_record, columns))
    return ToolResult(data={"count": len(charts)}, table=builder.build())


_EXPAND_COLUMNS = (tc.DIFFICULTY, tc.LEVEL, tc.CONST, tc.NOTE_COUNT)
_DEFAULT_EXPAND = "MAS"


@tool()
async def charts_of(
    ctx: ToolContext,
    table: Annotated[TableRef, TableNeeds(nonempty=[tc.SONG])],
    difficulty: DifficultyRef | None = None,
) -> ToolResult:
    """곡 표의 곡 단위 행(난이도 없음)을 그 곡의 채보 행으로 바꾼 새 표를 돌려준다. 난이도를 안 주면 MAS다
    (모든 난이도로 펼치지 않는다). 그 난이도가 없는 곡은 빠진다. 이미 채보인 행은 그대로 둔다.
    점수·보더·채보 링크처럼 난이도가 필요한 도구에 곡 표를 넘기기 전에 쓴다."""
    assert isinstance(table, WorkTable)
    code = _difficulty_code(difficulty) or _DEFAULT_EXPAND
    missing = 0
    expanded = 0

    def expand(row: Row) -> Sequence[Row] | None:
        nonlocal missing, expanded
        if row.cell(tc.DIFFICULTY).filled:
            return None
        song = ctx.songdb.song_by_title(str(row.cell(tc.SONG).raw))
        chart_record = None if song is None else song.charts.get(code)
        if song is None or chart_record is None or not chart_record.is_available:
            missing += 1
            return []
        expanded += 1
        cells = {**row.cells, **tc.chart_cells(song, chart_record, _EXPAND_COLUMNS)}
        return [Row(tc.chart_ref(song.title, code), MappingProxyType(cells))]

    result = expand_rows(table, expand, tc.common_columns(*_EXPAND_COLUMNS))
    steps = [f"{code}로 펼침"] if expanded else ["펼칠 곡 단위 행 없음"]
    if missing:
        steps.append(f"{code} 없는 곡 {missing}개 뺌")
    result = replace(result, steps=(*result.steps, *steps), unit="채보" if expanded else result.unit)
    if result.row_count == 0:
        return ToolResult(error=f"{code} 채보가 있는 곡이 없다(곡 {missing}개 뺌)")
    return ToolResult(data={"count": result.row_count, "difficulty": code, "expanded": expanded}, table=result)


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


_LINK = "채보 링크"


@tool()
async def add_links(
    ctx: ToolContext, table: Annotated[TableRef, TableNeeds(nonempty=[tc.SONG, tc.DIFFICULTY])]
) -> ToolResult:
    """채보 표에 "채보 링크" 칸을 더한 새 표를 돌려준다. 규칙은 chart_link와 같다(sdvx.in, 없으면 유튜브 검색)."""
    assert isinstance(table, WorkTable)
    mapped = 0

    def cells_for(row: Row) -> dict[str, Cell]:
        nonlocal mapped
        title, code = _row_key(row)
        song = ctx.songdb.song_by_title(title)
        chart_record = None if song is None else song.charts.get(code)
        if song is None or chart_record is None:
            return {_LINK: cell(None, "링크 없음")}
        link = ctx.songdb.chart_link(song, chart_record)
        mapped += int(link.has_mapping)
        return {_LINK: cell(link.url)}

    extended = extend_table(table, [Column(name=_LINK, label=_LINK)], cells_for, steps=["채보 링크"])
    return ToolResult(data={"count": extended.row_count, "with_mapping": mapped}, table=extended)


def _row_key(row: Row) -> tuple[str, str]:
    difficulty = str(row.cell(tc.DIFFICULTY).raw)
    return str(row.cell(tc.SONG).raw), LONG_TO_SHORT_DIFFICULTY.get(difficulty, difficulty)


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
