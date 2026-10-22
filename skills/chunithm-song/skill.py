# skills/chunithm-song/skill.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Annotated

from core.decorators import tool
from core.events import Section
from core.fuzzy import similar_names
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
# 검색 결과 표의 기본 칸은 곡·난이도·상수다. 나머지는 표에 있어도 글자 출력과 그림에서 숨기고, 물었을 때만
# `table_query`의 `columns`로 보인다(2026-10-22 결정).
_SEARCH_HIDDEN = (tc.LEVEL, tc.NOTE_COUNT, tc.VERSION, tc.ARTIST)
_NO_ARTIST_SONGS = "해당 작곡가의 곡이 없어."


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
    tc.add_columns(builder, *_SEARCH_COLUMNS, hidden_in_text=_SEARCH_HIDDEN)
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
        return ToolResult(data={"count": 0}, display=Display(content="해당 범위의 채보가 없어."))
    conditions = [tc.const_range_text(min_const, max_const), short_code, "신곡만" if new_only else None]
    return ToolResult(data={"count": len(hits)}, table=_chart_table(hits, conditions))


@tool(slash="버전")
async def find_by_version(
    ctx: ToolContext, version: str, difficulty: DifficultyRef | None = None
) -> ToolResult:
    """수록 버전으로 채보 목록을 상수순으로 찾는다. 난이도를 안 주면 그 버전의 모든 채보를
    난이도 가리지 않고 찾는다. version은 곡 DB의 버전 이름(VERSE, X-VERSE, X-VERSE-X 등)이어야 한다.
    Linked VERSE(링버) 같은 해금 시스템·모드 이름이면 쓰지 않는다."""
    short_code = _difficulty_code(difficulty)
    hits = ctx.songdb.find_by_version(version=version, difficulty=short_code)
    if not hits:
        return ToolResult(data={"count": 0}, display=Display(content="해당 버전의 채보가 없어."))
    return ToolResult(data={"count": len(hits)}, table=_chart_table(hits, [f"{version} 버전", short_code]))


@tool()
async def find_linked_verse(
    ctx: ToolContext, gate: str | None = None, difficulty: DifficultyRef | None = None
) -> ToolResult:
    """Linked VERSE(링크드 버스, 링버)의 해금곡 채보를 게이트별 묶음 표로 찾는다. Linked VERSE는 수록 버전이 아니라
    X-VERSE에서 시작한 해금 시스템이라 `find_by_version`으로는 찾을 수 없다. gate를 비우면 전부(ORIGIN, AIR, STAR,
    AMAZON, CRYSTAL, PARADISE, NEW, SUN, LUMINOUS, VERSE, 전도 악곡, UNIVERSE, ENDING), 난이도를 안 주면 곡마다 모든
    난이도다. 상수표나 그림을 달라고 하면 난이도를 MAS로 준다."""
    short_code = _difficulty_code(difficulty)
    try:
        hits = ctx.songdb.find_linked_verse(gate=gate, difficulty=short_code)
    except ValueError:
        return ToolResult(error=f"그런 게이트는 없어. 게이트: {', '.join(ctx.songdb.linked_verse_gates())}")
    if not hits:
        return ToolResult(data={"count": 0}, display=Display(content="해당하는 해금곡 채보가 없어."))
    builder = TableBuilder(unit="채보")
    tc.add_columns(builder, *_SEARCH_COLUMNS, hidden_in_text=_SEARCH_HIDDEN)
    builder.step(" ".join(c for c in ["Linked VERSE", gate, short_code] if c))
    current: str | None = None
    for hit in hits:
        if hit.gate != current:
            builder.group(hit.gate)
            current = hit.gate
        builder.row(
            tc.chart_ref(hit.song.title, hit.chart.difficulty),
            tc.chart_cells(hit.song, hit.chart, _SEARCH_COLUMNS),
        )
    return ToolResult(data={"count": len(hits), "songs": len({h.song.title for h in hits})}, table=builder.build())


@tool(slash="검색")
async def find_songs(
    ctx: ToolContext,
    min_const: float | None = None,
    max_const: float | None = None,
    level: str | None = None,
    version: str | None = None,
    difficulty: DifficultyRef | None = None,
    other_difficulty: DifficultyRef | None = None,
    min_note_count: int | None = None,
    max_note_count: int | None = None,
    new_only: bool = False,
) -> ToolResult:
    """상수 범위·레벨·수록 버전·난이도·노트 수 범위·신곡 여부를 한 번에 조합해 채보를 찾는다.
    `/상수`·`/버전`은 조건 하나(+난이도)만 받으니, 조건을 두 개 이상 동시에 걸 때만 이걸 쓴다.
    "마스터나 울티마"처럼 난이도를 둘 말했으면 difficulty와 other_difficulty에 하나씩 준다(둘 중 어느 쪽이든 맞는 채보).
    조건을 하나도 안 줬으면 범위가 너무 넓으니 바로 에러를 돌려주고, 조건을 더 물어본다.
    결과는 상수 오름차순 표다. 다른 순서가 필요하면 표에 `table_query`를 쓴다."""
    if not new_only and all(
        v is None
        for v in (min_const, max_const, level, version, difficulty, other_difficulty, min_note_count, max_note_count)
    ):
        return ToolResult(error="조건을 하나는 줘야 해(상수 범위, 레벨, 버전, 난이도, 노트 수, 신곡 중)")

    codes = [code for code in (_difficulty_code(difficulty), _difficulty_code(other_difficulty)) if code is not None]
    hits = ctx.songdb.find_songs(
        min_const=min_const, max_const=max_const, level=level, version=version,
        difficulty=codes or None, min_note_count=min_note_count, max_note_count=max_note_count,
        new_only=new_only,
    )
    if not hits:
        return ToolResult(data={"count": 0}, display=Display(content="조건에 맞는 채보가 없어."))
    conditions = [
        tc.const_range_text(min_const, max_const),
        f"레벨 {level}" if level else None,
        f"{version} 버전" if version else None,
        "·".join(dict.fromkeys(codes)) or None,
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
    artist = ctx.terms.artist_alias(artist) or artist  # 확인받아 저장된 작곡가 별칭이면 곡 DB 표기로
    songs = ctx.songdb.find_by_artist(name=artist)
    short_code = _difficulty_code(difficulty)
    condition = f"{artist} 작곡"
    if not songs:
        similar = similar_names(artist, ctx.songdb.all_artists(), limit=3, cutoff=0.6)
        if similar:
            return ToolResult(
                data={"count": 0, "similar_artists": similar},
                display=Display(content=f"{_NO_ARTIST_SONGS} 비슷한 작곡가: {', '.join(similar)}"),
            )

    if short_code is None:
        columns = (tc.SONG, tc.DIFFICULTY, tc.ARTIST, tc.VERSION)
        if not songs:
            return ToolResult(data={"count": 0}, display=Display(content=_NO_ARTIST_SONGS))
        builder = TableBuilder(unit="곡")
        tc.add_columns(builder, *columns, hidden_in_text=_SEARCH_HIDDEN).step(condition)
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
    tc.add_columns(builder, *columns, hidden_in_text=_SEARCH_HIDDEN).step(f"{condition} {short_code}")
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

    result = expand_rows(table, expand, tc.common_columns(*_EXPAND_COLUMNS, hidden_in_text=_SEARCH_HIDDEN))
    steps = [f"{code}로 펼침"] if expanded else ["펼칠 곡 단위 행 없음"]
    if missing:
        steps.append(f"{code} 없는 곡 {missing}개 뺌")
    result = replace(result, steps=(*result.steps, *steps), unit="채보" if expanded else result.unit)
    if result.row_count == 0:
        return ToolResult(error=f"{code} 채보가 있는 곡이 없어(곡 {missing}개 뺌)")
    return ToolResult(data={"count": result.row_count, "difficulty": code, "expanded": expanded}, table=result)


# 곡 단위 행에 남기는 칸. 나머지(난이도·레벨·상수·노트 수·점수·링크 등)는 채보마다 달라서 곡 단위에서는 비운다.
_SONG_LEVEL_COLUMNS = (tc.SONG, tc.VERSION, tc.ARTIST, tc.SONG_RAW_NAME)


@tool()
async def songs_of(ctx: ToolContext, table: Annotated[TableRef, TableNeeds(nonempty=[tc.SONG])]) -> ToolResult:
    """표의 채보 행을 곡 단위 행으로 합친 새 표를 돌려준다(`charts_of`의 반대). 같은 곡의 채보가 여럿이면 곡 하나로
    줄고, 난이도·레벨·상수·노트 수·점수처럼 채보마다 다른 칸은 빈다. 곡은 표에서 처음 나온 자리에 놓인다.
    "곡 이름만 한 번씩"처럼 곡이 중복되면 안 되는 요청에 쓴다. 곡 단위 행은 점수·보더·링크에 넘기려면 `charts_of`가 필요하다."""
    assert isinstance(table, WorkTable)
    seen: set[str] = set()
    merged = 0

    def to_song(row: Row) -> Sequence[Row] | None:
        nonlocal merged
        title = str(row.cell(tc.SONG).raw)
        if title in seen:
            merged += 1
            return []
        seen.add(title)
        cells = {name: row.cell(name) for name in _SONG_LEVEL_COLUMNS if name in row.cells}
        return [Row(tc.song_ref(title), MappingProxyType(cells))]

    result = expand_rows(table, to_song)
    steps = ["곡 단위로 합침"] + ([f"겹친 채보 {merged}개 합침"] if merged else [])
    result = replace(result, steps=(*result.steps, *steps), unit="곡")
    return ToolResult(data={"count": result.row_count, "merged": merged}, table=result)


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
    """곡 별칭을 봇 전역으로 등록한다(모든 서버·모든 플레이어에게 적용). 사용자가 그 곡이 맞다고 확인한 뒤에만 부른다."""
    assert isinstance(song, SongRecord)
    try:
        await ctx.songdb.add_alias(alias=alias, song_title=song.title, added_by=ctx.player_id)
    except ValueError as exc:
        return ToolResult(error=str(exc))
    return ToolResult(
        data={"alias": alias, "song": song.title},
        display=Display(content=f"'{alias}' → {song.title}로 등록했어."),
        display_mode="optional",
    )


@tool(slash="별칭 삭제", mutates=True)
async def alias_remove(ctx: ToolContext, alias: str) -> ToolResult:
    """별칭을 지운다. 추가한 사람이나 admin만 할 수 있다."""
    row = await ctx.songdb.get_alias(alias=alias)
    if row is None:
        return ToolResult(error="그런 별칭이 없어")
    if row.added_by != ctx.player_id and "admin" not in ctx.tags:
        return ToolResult(error="추가한 사람이나 admin만 지울 수 있어")
    await ctx.songdb.remove_alias(alias=alias)
    return ToolResult(data={"alias": alias}, display=Display(content=f"'{alias}' 별칭을 지웠어."), display_mode="optional")
