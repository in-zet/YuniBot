# skills/settings/skill.py
from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated

from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError

from core.decorators import tool
from core.tables import EMPTY_CELL, RowRef, TableBuilder, TableNeeds, WorkTable, cell
from core.tool_types import (
    Display,
    GameRef,
    SongQuery,
    TableRef,
    ToolContext,
    ToolResult,
)
from services.game import table_columns as tc
from services.game.net_settings import (
    ChangeNotAppliedError,
    SettingField,
    labeled,
    settings_line,
)
from services.game.registry import GAME_PROFILES, profile_for
from services.game.songdb import SongRecord

_UNKNOWN_SONG = "settings.unknown_song"
# 일괄 추가 결과에 곡 이름을 늘어놓을 때 자르는 개수.
_DISPLAY_LIMIT = 15

# 즐겨찾기 곡을 가리키는 값: CHUNITHM-NET은 musicId(int), maimai DX NET은 곡 제목(사이트의 곡 값이 불투명하다).
_FavoriteKey = int | str


class _NetFailed(Exception):
    """NET 호출 실패를 사용자 문장으로 바꾼 것."""

    def __init__(self, result: ToolResult) -> None:
        super().__init__(result.error)
        self.result = result


def _fields(ctx: ToolContext) -> tuple[SettingField, ...]:
    return profile_for(ctx.game).settings_fields


async def _net[T](ctx: ToolContext, call: Awaitable[T], *, doing: str) -> T:
    """NET 호출. 로그인 없음·점검·만료와 반영 실패(`ChangeNotAppliedError`)·잘못된 값(ValueError)은 `_NetFailed`로."""
    net_name = profile_for(ctx.game).net_name
    try:
        return await call
    except ConnectionError:
        raise _NetFailed(ToolResult(error=f"로그인이 안 돼 있어서 {doing}.")) from None
    except MaintenanceError:
        raise _NetFailed(ToolResult(error=f"{net_name}이 점검 중이라 {doing}.")) from None
    except AuthenticationError:
        raise _NetFailed(ToolResult(error=f"로그인이 만료돼서 {doing}. 다시 연결해 줘.")) from None
    except (ValueError, ChangeNotAppliedError) as e:
        raise _NetFailed(ToolResult(error=str(e))) from None


def _favorite_key(ctx: ToolContext, song: SongRecord) -> _FavoriteKey:
    return song.title if ctx.game == "maimai" else song.id


def _song_for_key(ctx: ToolContext, key: _FavoriteKey) -> SongRecord | None:
    return ctx.songdb.song_by_title(key) if isinstance(key, str) else ctx.songdb.by_id(key)


@tool(cost="network")
async def current_settings(ctx: ToolContext, game: GameRef | None = None) -> ToolResult:
    """지금 계정의 인게임 설정을 NET(CHUNITHM-NET·maimai DX NET)에서 바로 읽어 보여준다."""
    try:
        settings = await _net(ctx, ctx.chunithm_net.get_arcade_settings(ctx.player_id, ctx=ctx), doing="설정을 못 읽었어")
    except _NetFailed as failed:
        return failed.result
    fields = _fields(ctx)
    return ToolResult(
        data={"settings": labeled(settings.named, fields)},
        display=Display(content=settings_line(settings.named, fields)),
        display_mode="required",
    )


@tool(cost="network", mutates=True)
async def change_settings(
    ctx: ToolContext,
    speed: str | None = None,
    field_wall_position: str | None = None,
    touch_speed: str | None = None,
    slide_timing: str | None = None,
    play_timing_offset: str | None = None,
    judge_timing_offset: str | None = None,
    guide_sound: str | None = None,
    game: GameRef | None = None,
) -> ToolResult:
    """말한 인게임 설정 항목만 바꾼다. 값은 NET 설정 페이지에 실제로 있는 표시값 그대로 받는다(예: 스피드 "9.00", 가이드음 "5").
    공통: speed(CHUNITHM 스피드, maimai 노트 속도), play_timing_offset(판정 타이밍 A), judge_timing_offset(판정 타이밍 B),
    guide_sound(가이드음). CHUNITHM만: field_wall_position(필드 월). maimai만: touch_speed(터치 속도), slide_timing(슬라이드 타이밍)."""
    requested = {
        "speed": speed, "field_wall_position": field_wall_position, "touch_speed": touch_speed,
        "slide_timing": slide_timing, "play_timing_offset": play_timing_offset,
        "judge_timing_offset": judge_timing_offset, "guide_sound": guide_sound,
    }
    changes = {name: value for name, value in requested.items() if value is not None}
    if not changes:
        return ToolResult(error="바꿀 항목을 말해줘.")
    fields = _fields(ctx)
    known = {f.attr for f in fields}
    foreign = [name for name in changes if name not in known]
    if foreign:
        every_label = {f.attr: f.label for profile in GAME_PROFILES.values() for f in profile.settings_fields}
        names = ", ".join(every_label.get(name, name) for name in foreign)
        return ToolResult(error=f"{profile_for(ctx.game).label} 설정에는 {names} 항목이 없어.")

    try:
        settings = await _net(
            ctx, ctx.chunithm_net.update_arcade_settings(ctx.player_id, changes=changes, ctx=ctx), doing="설정을 못 바꿨어"
        )
    except _NetFailed as failed:
        return failed.result
    labels = {f.attr: f.label for f in fields}
    return ToolResult(
        data={"settings": labeled(settings.named, fields), "changed": [labels[key] for key in changes]},
        display=Display(content="바꿨어 — " + settings_line(settings.named, fields)),
        display_mode="required",
    )


@tool(cost="network")
async def list_favorites(ctx: ToolContext, game: GameRef | None = None) -> ToolResult:
    """지금 즐겨찾기로 등록된 곡 목록을 표로 보여준다. 다른 곡 표를 즐겨찾기에 더할 때는 add_favorites를 쓴다."""
    try:
        keys = await _net(ctx, ctx.chunithm_net.get_favorite_music(ctx.player_id, ctx=ctx), doing="즐겨찾기를 못 읽었어")
    except _NetFailed as failed:
        return failed.result

    if not keys:
        return ToolResult(data={"titles": []}, display=Display(content="즐겨찾기에 등록된 곡이 없어."))

    titles: list[str] = []
    builder = TableBuilder(unit="곡")
    tc.add_columns(builder, tc.SONG, tc.SONG_RAW_NAME, tc.ARTIST, tc.VERSION)
    builder.step("즐겨찾기")
    for key in keys:
        song = _song_for_key(ctx, key)
        if song is None:
            # 곡 DB에 없는 곡은 곡 칸을 비우고 원문 칸에 적는다(곡 칸이 필요한 도구에서는 빠진다).
            unknown = key if isinstance(key, str) else f"(모르는 곡 id {key})"
            titles.append(unknown)
            builder.row(RowRef(_UNKNOWN_SONG, str(key)), {tc.SONG: EMPTY_CELL, tc.SONG_RAW_NAME: cell(unknown)})
            continue
        titles.append(song.title)
        builder.row(tc.song_ref(song.title), tc.song_cells(song, (tc.SONG, tc.ARTIST, tc.VERSION)))
    return ToolResult(data={"titles": titles}, table=builder.build())


async def _read_favorites(ctx: ToolContext) -> list[_FavoriteKey]:
    return await _net(ctx, ctx.chunithm_net.get_favorite_music(ctx.player_id, ctx=ctx), doing="즐겨찾기를 못 바꿨어")


async def _write_favorites(ctx: ToolContext, keys: list[_FavoriteKey]) -> None:
    """즐겨찾기 전체 목록을 쓴다. 서비스가 쓴 뒤 다시 읽어 확인하므로, 예외 없이 끝나면 실제로 반영된 것이다."""
    await _net(ctx, ctx.chunithm_net.set_favorite_music(ctx.player_id, keys, ctx=ctx), doing="즐겨찾기를 못 바꿨어")


@tool(cost="network", mutates=True)
async def add_favorite(ctx: ToolContext, song: SongQuery, game: GameRef | None = None) -> ToolResult:
    """즐겨찾기에 곡 하나를 더한다."""
    assert isinstance(song, SongRecord)
    key = _favorite_key(ctx, song)
    try:
        keys = await _read_favorites(ctx)
        if key in keys:
            return ToolResult(
                data={"title": song.title, "added": False}, display=Display(content=f"{song.title}은(는) 이미 즐겨찾기에 있어.")
            )
        await _write_favorites(ctx, [*keys, key])
    except _NetFailed as failed:
        return failed.result
    return ToolResult(data={"title": song.title, "added": True}, display=Display(content=f"{song.title}을(를) 즐겨찾기에 더했어."))


@tool(cost="network", mutates=True)
async def remove_favorite(ctx: ToolContext, song: SongQuery, game: GameRef | None = None) -> ToolResult:
    """즐겨찾기에서 곡 하나를 뺀다."""
    assert isinstance(song, SongRecord)
    key = _favorite_key(ctx, song)
    try:
        keys = await _read_favorites(ctx)
        if key not in keys:
            return ToolResult(
                data={"title": song.title, "removed": False}, display=Display(content=f"{song.title}은(는) 즐겨찾기에 없어.")
            )
        await _write_favorites(ctx, [k for k in keys if k != key])
    except _NetFailed as failed:
        return failed.result
    return ToolResult(data={"title": song.title, "removed": True}, display=Display(content=f"{song.title}을(를) 즐겨찾기에서 뺐어."))


@tool(cost="network", mutates=True, confirm="bulk")
async def add_favorites(
    ctx: ToolContext, table: Annotated[TableRef, TableNeeds(nonempty=[tc.SONG])], *, dry_run: bool = False
) -> ToolResult:
    """작업 표의 곡(곡 칸이 있는 행, 채보 행이면 그 곡)을 즐겨찾기에 일괄로 더한다. 이미 즐겨찾기에 있는 곡은 뺀다.
    NET에서 지금 목록을 읽고 더한 전체 목록을 한 번에 쓴다(표의 게임으로). 대상이 2개 이상이면 실행기가 먼저 개수를
    보여 주고 확인을 받는다."""
    assert isinstance(table, WorkTable)
    try:
        keys = await _read_favorites(ctx)
    except _NetFailed as failed:
        return failed.result

    titles = list(dict.fromkeys(str(row.cell(tc.SONG).raw) for row in table.rows))
    new_keys: list[_FavoriteKey] = []
    new_titles: list[str] = []
    already = unknown = 0
    for title in titles:
        song = ctx.songdb.song_by_title(title)
        if song is None:
            unknown += 1
            continue
        key = _favorite_key(ctx, song)
        if key in keys or key in new_keys:
            already += 1
        else:
            new_keys.append(key)
            new_titles.append(song.title)

    notes = [step for step in table.steps if "뺌" in step]
    if already:
        notes.append(f"{already}개는 이미 즐겨찾기에 있음")
    if unknown:
        notes.append(f"{unknown}개는 곡 데이터에 없음")
    note_text = f" ({'; '.join(notes)})" if notes else ""
    shown = _shown(new_titles)
    data = {"count": len(new_titles), "titles": new_titles, "already": already, "unknown": unknown}

    if not new_titles:
        return ToolResult(data=data, display=Display(content=f"더할 곡이 없어.{note_text}"))
    if dry_run:
        return ToolResult(data=data, display=Display(content=f"즐겨찾기에 {len(new_titles)}곡을 더할게: {shown}{note_text}"))

    try:
        await _write_favorites(ctx, [*keys, *new_keys])
    except _NetFailed as failed:
        return failed.result
    return ToolResult(data=data, display=Display(content=f"즐겨찾기에 {len(new_titles)}곡을 더했어: {shown}{note_text}"))


@tool(cost="network", mutates=True, confirm="bulk")
async def remove_favorites(
    ctx: ToolContext, table: Annotated[TableRef, TableNeeds(nonempty=[tc.SONG])], *, dry_run: bool = False
) -> ToolResult:
    """작업 표의 곡(곡 칸이 있는 행, 채보 행이면 그 곡)을 즐겨찾기에서 일괄로 뺀다. 즐겨찾기에 없는 곡은 건너뛴다.
    조건("15.0 곡들")이 있으면 그 조건의 곡 표를 바로 준다 — 즐겨찾기에 없는 곡은 건너뛰므로 list_favorites를 먼저
    부르지 않는다. list_favorites 표를 거른 표도 된다. 전부 지울 때는 clear_favorites를 쓴다.
    대상이 2개 이상이면 실행기가 먼저 개수를 보여 주고 확인을 받는다."""
    assert isinstance(table, WorkTable)
    try:
        keys = await _read_favorites(ctx)
    except _NetFailed as failed:
        return failed.result

    drop_keys: list[_FavoriteKey] = []
    drop_titles: list[str] = []
    absent = 0
    for title in dict.fromkeys(str(row.cell(tc.SONG).raw) for row in table.rows):
        song = ctx.songdb.song_by_title(title)
        key = _favorite_key(ctx, song) if song is not None else None
        if key is None or key not in keys:
            absent += 1
        elif key not in drop_keys:
            drop_keys.append(key)
            drop_titles.append(title)

    note_text = f" ({absent}개는 즐겨찾기에 없음)" if absent else ""
    data = {"count": len(drop_titles), "titles": drop_titles, "absent": absent}
    if not drop_titles:
        return ToolResult(data=data, display=Display(content=f"뺄 곡이 없어.{note_text}"))
    shown = _shown(drop_titles)
    if dry_run:
        return ToolResult(data=data, display=Display(content=f"즐겨찾기에서 {len(drop_titles)}곡을 뺄게: {shown}{note_text}"))

    try:
        await _write_favorites(ctx, [key for key in keys if key not in drop_keys])
    except _NetFailed as failed:
        return failed.result
    return ToolResult(data=data, display=Display(content=f"즐겨찾기에서 {len(drop_titles)}곡을 뺐어: {shown}{note_text}"))


@tool(cost="network", mutates=True, confirm="bulk")
async def clear_favorites(ctx: ToolContext, game: GameRef | None = None, *, dry_run: bool = False) -> ToolResult:
    """즐겨찾기를 전부 비운다. "즐겨찾기 다 지워"·"전부 삭제"처럼 전체를 분명히 말했을 때만 부른다. 일부만이면
    remove_favorites에 표를 준다. 2곡 이상이면 실행기가 먼저 개수를 보여 주고 확인을 받는다."""
    try:
        keys = await _read_favorites(ctx)
    except _NetFailed as failed:
        return failed.result

    data = {"count": len(keys)}
    if not keys:
        return ToolResult(data=data, display=Display(content="즐겨찾기가 이미 비어 있어."))
    if dry_run:
        return ToolResult(data=data, display=Display(content=f"즐겨찾기 {len(keys)}곡을 전부 지울게."))
    try:
        await _write_favorites(ctx, [])
    except _NetFailed as failed:
        return failed.result
    return ToolResult(data=data, display=Display(content=f"즐겨찾기 {len(keys)}곡을 전부 지웠어."))


def _shown(titles: list[str]) -> str:
    return ", ".join(titles[:_DISPLAY_LIMIT]) + (f" 외 {len(titles) - _DISPLAY_LIMIT}개" if len(titles) > _DISPLAY_LIMIT else "")
