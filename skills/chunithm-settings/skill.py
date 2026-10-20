# skills/chunithm-settings/skill.py
from __future__ import annotations

from typing import Annotated

from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError

from core.decorators import tool
from core.tables import EMPTY_CELL, RowRef, TableBuilder, TableNeeds, WorkTable, cell
from core.tool_types import Display, SongQuery, TableRef, ToolContext, ToolResult
from services.chunithm import table_columns as tc
from services.chunithm.arcade_settings import (
    FIELD_LABELS,
    named_from_settings,
    settings_line,
)
from services.chunithm.songdb import SongRecord

_UNKNOWN_SONG = "chunithm-settings.unknown_song"
# 일괄 추가 결과에 곡 이름을 늘어놓을 때 자르는 개수.
_DISPLAY_LIMIT = 15
_CHANGEABLE_FIELDS = ("speed", "field_wall_position", "play_timing_offset", "judge_timing_offset", "guide_sound")


def _labeled(named: dict[str, str]) -> dict[str, str]:
    """`play_timing_offset`·`judge_timing_offset` 같은 내부 이름은 모델이 A·B에 거꾸로 짝지은 적이 있다
    (2026-10-20 실기: 판정 A 0.0·B 0.8을 A 0.8·B 0.0으로 말함). data는 화면에 보이는 이름(판정 타이밍 A)으로 낸다."""
    return {FIELD_LABELS[key]: value for key, value in named.items()}


@tool(slash="현재설정", cost="network")
async def current_settings(ctx: ToolContext) -> ToolResult:
    """지금 계정의 인게임 설정을 CHUNITHM-NET에서 바로 읽어 보여준다."""
    try:
        settings = await ctx.chunithm_net.get_arcade_settings(ctx.player_id, ctx=ctx)
    except ConnectionError:
        return ToolResult(error="로그인이 안 돼 있어서 설정을 못 읽었어.")
    except MaintenanceError:
        return ToolResult(error="CHUNITHM-NET이 점검 중이라 설정을 못 읽었어.")
    except AuthenticationError:
        return ToolResult(error="로그인이 만료돼서 설정을 못 읽었어. 다시 연결해 줘.")

    named = named_from_settings(settings)
    return ToolResult(
        data={"settings": _labeled(named)},
        display=Display(content=settings_line(named)),
        display_mode="required",
    )


@tool(slash="게임설정 변경", cost="network", mutates=True)
async def change_settings(
    ctx: ToolContext,
    speed: str | None = None,
    field_wall_position: str | None = None,
    play_timing_offset: str | None = None,
    judge_timing_offset: str | None = None,
    guide_sound: str | None = None,
) -> ToolResult:
    """스피드·필드 월·판정 타이밍 A·B·가이드음 중 말한 항목만 바꾼다. 값은 CHUNITHM-NET
    설정 페이지에 실제로 있는 표시값 그대로 받는다(예: 스피드 "9.00", 가이드음 "5").
    play_timing_offset는 판정 타이밍 A, judge_timing_offset는 판정 타이밍 B다."""
    changes = {
        name: value
        for name, value in zip(_CHANGEABLE_FIELDS, (speed, field_wall_position, play_timing_offset, judge_timing_offset, guide_sound), strict=True)
        if value is not None
    }
    if not changes:
        return ToolResult(error="바꿀 항목을 말해줘.")

    try:
        settings = await ctx.chunithm_net.update_arcade_settings(ctx.player_id, changes=changes, ctx=ctx)
    except ConnectionError:
        return ToolResult(error="로그인이 안 돼 있어서 설정을 못 바꿨어.")
    except MaintenanceError:
        return ToolResult(error="CHUNITHM-NET이 점검 중이라 설정을 못 바꿨어.")
    except AuthenticationError:
        return ToolResult(error="로그인이 만료돼서 설정을 못 바꿨어. 다시 연결해 줘.")
    except ValueError as e:
        return ToolResult(error=str(e))

    named = named_from_settings(settings)
    return ToolResult(
        data={"settings": _labeled(named), "changed": [FIELD_LABELS[key] for key in changes]},
        display=Display(content="바꿨어 — " + settings_line(named)),
        display_mode="required",
    )


@tool(slash="즐겨찾기 목록", cost="network")
async def list_favorites(ctx: ToolContext) -> ToolResult:
    """지금 즐겨찾기로 등록된 곡 목록을 표로 보여준다. 다른 곡 표를 즐겨찾기에 더할 때는 add_favorites를 쓴다."""
    try:
        ids = await ctx.chunithm_net.get_favorite_music(ctx.player_id, ctx=ctx)
    except ConnectionError:
        return ToolResult(error="로그인이 안 돼 있어서 즐겨찾기를 못 읽었어.")
    except MaintenanceError:
        return ToolResult(error="CHUNITHM-NET이 점검 중이라 즐겨찾기를 못 읽었어.")
    except AuthenticationError:
        return ToolResult(error="로그인이 만료돼서 즐겨찾기를 못 읽었어. 다시 연결해 줘.")

    if not ids:
        return ToolResult(data={"titles": []}, display=Display(content="즐겨찾기에 등록된 곡이 없어."))

    titles: list[str] = []
    builder = TableBuilder(unit="곡")
    tc.add_columns(builder, tc.SONG, tc.SONG_RAW_NAME, tc.ARTIST, tc.VERSION)
    builder.step("즐겨찾기")
    for music_id in ids:
        song = ctx.songdb.by_id(music_id)
        if song is None:
            # 곡 DB에 없는 곡은 곡 칸을 비우고 원문 칸에 id를 적는다(곡 칸이 필요한 도구에서는 빠진다).
            unknown = f"(모르는 곡 id {music_id})"
            titles.append(unknown)
            builder.row(
                RowRef(_UNKNOWN_SONG, str(music_id)),
                {tc.SONG: EMPTY_CELL, tc.SONG_RAW_NAME: cell(unknown)},
            )
            continue
        titles.append(song.title)
        builder.row(tc.song_ref(song.title), tc.song_cells(song, (tc.SONG, tc.ARTIST, tc.VERSION)))
    return ToolResult(data={"titles": titles}, table=builder.build())


@tool(slash="즐겨찾기 추가", cost="network", mutates=True)
async def add_favorite(ctx: ToolContext, song: SongQuery) -> ToolResult:
    """즐겨찾기에 곡 하나를 더한다."""
    assert isinstance(song, SongRecord)
    try:
        ids = await ctx.chunithm_net.get_favorite_music(ctx.player_id, ctx=ctx)
    except ConnectionError:
        return ToolResult(error="로그인이 안 돼 있어서 즐겨찾기를 못 바꿨어.")
    except MaintenanceError:
        return ToolResult(error="CHUNITHM-NET이 점검 중이라 즐겨찾기를 못 바꿨어.")
    except AuthenticationError:
        return ToolResult(error="로그인이 만료돼서 즐겨찾기를 못 바꿨어. 다시 연결해 줘.")

    if song.id in ids:
        return ToolResult(data={"title": song.title, "added": False}, display=Display(content=f"{song.title}은(는) 이미 즐겨찾기에 있어."))

    await ctx.chunithm_net.set_favorite_music(ctx.player_id, [*ids, song.id], ctx=ctx)
    return ToolResult(
        data={"title": song.title, "added": True},
        display=Display(content=f"{song.title}을(를) 즐겨찾기에 더했어."),
    )


@tool(slash="즐겨찾기 삭제", cost="network", mutates=True)
async def remove_favorite(ctx: ToolContext, song: SongQuery) -> ToolResult:
    """즐겨찾기에서 곡 하나를 뺀다."""
    assert isinstance(song, SongRecord)
    try:
        ids = await ctx.chunithm_net.get_favorite_music(ctx.player_id, ctx=ctx)
    except ConnectionError:
        return ToolResult(error="로그인이 안 돼 있어서 즐겨찾기를 못 바꿨어.")
    except MaintenanceError:
        return ToolResult(error="CHUNITHM-NET이 점검 중이라 즐겨찾기를 못 바꿨어.")
    except AuthenticationError:
        return ToolResult(error="로그인이 만료돼서 즐겨찾기를 못 바꿨어. 다시 연결해 줘.")

    if song.id not in ids:
        return ToolResult(data={"title": song.title, "removed": False}, display=Display(content=f"{song.title}은(는) 즐겨찾기에 없어."))

    await ctx.chunithm_net.set_favorite_music(ctx.player_id, [i for i in ids if i != song.id], ctx=ctx)
    return ToolResult(
        data={"title": song.title, "removed": True},
        display=Display(content=f"{song.title}을(를) 즐겨찾기에서 뺐어."),
    )


@tool(cost="network", mutates=True, confirm="bulk")
async def add_favorites(
    ctx: ToolContext, table: Annotated[TableRef, TableNeeds(nonempty=[tc.SONG])], *, dry_run: bool = False
) -> ToolResult:
    """작업 표의 곡(곡 칸이 있는 행, 채보 행이면 그 곡)을 즐겨찾기에 일괄로 더한다. 이미 즐겨찾기에 있는 곡은 뺀다.
    CHUNITHM-NET에 지금 목록을 읽고 더한 전체 목록을 한 번에 쓴다. 대상이 2개 이상이면 실행기가 먼저 개수를 보여
    주고 확인을 받는다."""
    assert isinstance(table, WorkTable)
    try:
        ids = await ctx.chunithm_net.get_favorite_music(ctx.player_id, ctx=ctx)
    except ConnectionError:
        return ToolResult(error="로그인이 안 돼 있어서 즐겨찾기를 못 바꿨어.")
    except MaintenanceError:
        return ToolResult(error="CHUNITHM-NET이 점검 중이라 즐겨찾기를 못 바꿨어.")
    except AuthenticationError:
        return ToolResult(error="로그인이 만료돼서 즐겨찾기를 못 바꿨어. 다시 연결해 줘.")

    titles = list(dict.fromkeys(str(row.cell(tc.SONG).raw) for row in table.rows))
    new_ids: list[int] = []
    new_titles: list[str] = []
    already = unknown = 0
    for title in titles:
        song = ctx.songdb.song_by_title(title)
        if song is None:
            unknown += 1
        elif song.id in ids or song.id in new_ids:
            already += 1
        else:
            new_ids.append(song.id)
            new_titles.append(song.title)

    notes = [step for step in table.steps if "뺌" in step]
    if already:
        notes.append(f"{already}개는 이미 즐겨찾기에 있음")
    if unknown:
        notes.append(f"{unknown}개는 곡 데이터에 없음")
    note_text = f" ({'; '.join(notes)})" if notes else ""
    shown = ", ".join(new_titles[:_DISPLAY_LIMIT]) + (
        f" 외 {len(new_titles) - _DISPLAY_LIMIT}개" if len(new_titles) > _DISPLAY_LIMIT else ""
    )
    data = {"count": len(new_titles), "titles": new_titles, "already": already, "unknown": unknown}

    if not new_titles:
        return ToolResult(data=data, display=Display(content=f"더할 곡이 없어.{note_text}"))
    if dry_run:
        return ToolResult(data=data, display=Display(content=f"즐겨찾기에 {len(new_titles)}곡을 더할게: {shown}{note_text}"))

    try:
        await ctx.chunithm_net.set_favorite_music(ctx.player_id, [*ids, *new_ids], ctx=ctx)
    except MaintenanceError:
        return ToolResult(error="CHUNITHM-NET이 점검 중이라 즐겨찾기를 못 바꿨어.")
    except AuthenticationError:
        return ToolResult(error="로그인이 만료돼서 즐겨찾기를 못 바꿨어. 다시 연결해 줘.")
    except ValueError as e:
        return ToolResult(error=str(e))
    return ToolResult(data=data, display=Display(content=f"즐겨찾기에 {len(new_titles)}곡을 더했어: {shown}{note_text}"))
