# skills/notes/skill.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

import aiosqlite

from core.decorators import on, tool
from core.events import Section
from core.tables import (
    EMPTY_CELL,
    Cell,
    Column,
    Row,
    RowRef,
    TableBuilder,
    TableNeeds,
    WorkTable,
    cell,
    extend_table,
)
from core.tool_types import (
    DifficultyRef,
    Display,
    GameRef,
    SongQuery,
    TableRef,
    ToolContext,
    ToolResult,
)
from services.game import table_columns as tc
from services.game.registry import profile_for
from services.game.songdb import SongRecord
from services.game.terms import ResolvedDifficulty

# 남의 공개 메모를 한 번에 보여주는 개수(최근 것부터). 더 있으면 개수만 알려준다(더보기
# 버튼 자체는 다음 단계).
_OTHERS_LIMIT = 3
# chunithm.plan.requested 페이로드에는 후보곡 목록이 없다(결정 사항: Task 9/10/11 공통
# 전제, goals의 on_plan_requested와 같은 전제) — 내 메모 중 최근 것 몇 개만 보여준다.
_PLAN_LIMIT = 5
_SONG_VIEW_ORDER = 20
_PLAN_ORDER = 20

_NOTE = "notes.note"
_N_BODY = "메모"
_N_PUBLIC = "공개"
_N_UPDATED = "수정 시각"
_NOTE_FORMATS = ("{곡} {종류} {난이도}  ({공개})  {메모}", "{곡} {난이도}  ({공개})  {메모}", "{곡}  ({공개})  {메모}")


def _difficulty_code(difficulty: DifficultyRef | None) -> str:
    """난이도 인자를 저장용 짧은 코드로 바꾼다. 비어 있으면 곡 전체 메모(빈 문자열 — 결정
    사항 N: SQLite 복합 PK에서 NULL은 서로 다른 값으로 취급되어 덮어쓰기가 안 되므로 빈
    문자열을 쓴다)."""
    if difficulty is None:
        return ""
    assert isinstance(difficulty, ResolvedDifficulty)
    return tc.LONG_TO_SHORT_DIFFICULTY.get(difficulty.canonical, difficulty.canonical)


class _NoteTargetError(Exception):
    """메모 대상 채보를 정할 수 없을 때(채보 종류를 모르겠거나 없는 채보)의 안내 문장."""


def _chart_type_for(ctx: ToolContext, song: SongRecord, code: str, chart_type: str | None) -> str:
    """메모를 달 채보 종류. 곡 전체 메모(난이도 없음)와 종류가 없는 게임은 ""다. 종류가 있는 게임에서 난이도를 말했는데
    종류를 안 말했으면 그 난이도에 채보가 하나일 때만 정해 준다."""
    profile = profile_for(ctx.game)
    if not code or profile.chart_types == ("",):
        return ""
    if chart_type:
        wanted = chart_type.strip().upper()
        if wanted not in profile.chart_types:
            raise _NoteTargetError(f"채보 종류는 {', '.join(profile.chart_types)} 중 하나여야 해")
        if not any(c.chart_type == wanted and c.difficulty == code for c in song.charts.values()):
            raise _NoteTargetError(f"{song.title}에는 {wanted} {code} 채보가 없어")
        return wanted
    types = [c.chart_type for c in song.charts.values() if c.difficulty == code and c.is_available]
    if not types:
        raise _NoteTargetError(f"{song.title}에는 {code} 채보가 없어")
    if len(types) > 1:
        raise _NoteTargetError(f"{song.title} {code}는 {' / '.join(types)} 둘 다 있어. 어느 쪽 메모인지 알려 줘")
    return types[0]


def _note_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {"body": row["body"], "public": bool(row["public"]), "updated_at": row["updated_at"]}


async def _fetch_note(
    ctx: ToolContext, *, player_id: str, song_title: str, difficulty: str, chart_type: str
) -> dict[str, Any] | None:
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT body, public, updated_at FROM notes_notes "
        "WHERE player_id = ? AND game = ? AND song_title = ? AND chart_type = ? AND difficulty = ?",
        (player_id, ctx.game, song_title, chart_type, difficulty),
    )
    row = await cursor.fetchone()
    return None if row is None else _note_dict(row)


async def _fetch_public_others(
    ctx: ToolContext, *, song_title: str, difficulty: str, chart_type: str, exclude_player_id: str, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """공개된 남의 메모를 최근 것부터 돌려준다. (보여줄 목록, 전체 개수)."""
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT player_id, body, updated_at FROM notes_notes "
        "WHERE game = ? AND song_title = ? AND chart_type = ? AND difficulty = ? AND public = 1 AND player_id != ? "
        "ORDER BY updated_at DESC",
        (ctx.game, song_title, chart_type, difficulty, exclude_player_id),
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    total = len(rows)
    shown: list[dict[str, Any]] = [
        {"player_id": r["player_id"], "body": r["body"], "updated_at": r["updated_at"]} for r in rows[:limit]
    ]
    return shown, total


def _head(song_title: str, difficulty: str, chart_type: str = "") -> str:
    label = " ".join(part for part in (chart_type, difficulty) if part)
    return f"{song_title} {label}" if label else song_title


def _others_lines(others: list[dict[str, Any]], others_total: int) -> list[str]:
    lines = [f"{o['player_id']}의 메모\n{o['body']}" for o in others]
    if others_total > len(others):
        lines.append(f"...외 {others_total - len(others)}개 더 있음")
    return lines


# ---------------------------------------------------------------- 도구


@tool(slash="메모 쓰기", mutates=True)
async def set_note(
    ctx: ToolContext,
    song: SongQuery,
    text: str,
    difficulty: DifficultyRef | None = None,
    append: bool = False,
    public: bool | None = None,
    chart_type: str | None = None,
    game: GameRef | None = None,
) -> ToolResult:
    """곡(또는 채보)에 메모를 쓴다. 난이도를 비우면 곡 전체 메모다. 같은 곳에 다시 쓰면
    덮어쓰고, append=True면 줄바꿈으로 이어 붙인다. public=True면 다른 사람도 곡 조회
    조각에서 볼 수 있다. public을 주지 않으면 이미 있는 메모는 공개 여부를 그대로 두고, 새
    메모는 비공개로 만든다. 본인 메모만 바꾼다. maimai는 같은 난이도에 STD/DX가 둘 있는 곡이면 chart_type
    (STD 또는 DX)을 줘야 한다."""
    assert isinstance(song, SongRecord)
    assert ctx.db is not None
    code = _difficulty_code(difficulty)
    try:
        kind = _chart_type_for(ctx, song, code, chart_type)
    except _NoteTargetError as exc:
        return ToolResult(error=str(exc))
    existing = await _fetch_note(
        ctx, player_id=ctx.player_id, song_title=song.title, difficulty=code, chart_type=kind
    )
    body = f"{existing['body']}\n{text}" if append and existing is not None else text
    if public is None:
        public = bool(existing["public"]) if existing is not None else False
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        "INSERT INTO notes_notes (player_id, game, song_title, chart_type, difficulty, body, public, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (player_id, game, song_title, chart_type, difficulty) "
        "DO UPDATE SET body = excluded.body, public = excluded.public, updated_at = excluded.updated_at",
        (ctx.player_id, ctx.game, song.title, kind, code, body, int(public), now),
    )
    await ctx.db.commit()
    return ToolResult(
        data={"song_title": song.title, "difficulty": code, "chart_type": kind, "body": body, "public": public},
        display=Display(content=f"{_head(song.title, code, kind)} 메모를 저장했어."),
        display_mode="optional",
    )


@tool(slash="메모 보기")
async def get_note(
    ctx: ToolContext, song: SongQuery, difficulty: DifficultyRef | None = None, chart_type: str | None = None,
    game: GameRef | None = None,
) -> ToolResult:
    """내 메모와, 공개된 다른 사람의 메모(최근 3개 + 더 있으면 개수)를 함께 보여준다. maimai는 같은 난이도에
    STD/DX가 둘이면 chart_type을 준다."""
    assert isinstance(song, SongRecord)
    code = _difficulty_code(difficulty)
    try:
        kind = _chart_type_for(ctx, song, code, chart_type)
    except _NoteTargetError as exc:
        return ToolResult(error=str(exc))
    mine = await _fetch_note(
        ctx, player_id=ctx.player_id, song_title=song.title, difficulty=code, chart_type=kind
    )
    others, others_total = await _fetch_public_others(
        ctx, song_title=song.title, difficulty=code, chart_type=kind, exclude_player_id=ctx.player_id,
        limit=_OTHERS_LIMIT,
    )

    head = _head(song.title, code, kind)
    lines: list[str] = []
    if mine is not None:
        lines.append(f"내 메모\n{mine['body']}")
    lines += _others_lines(others, others_total)
    content = "\n\n".join([head, *lines]) if lines else f"{head}에 메모가 없어."

    return ToolResult(
        data={
            "song_title": song.title, "difficulty": code, "chart_type": kind, "mine": mine, "others": others,
            "others_total": others_total, "has_more": others_total > len(others),
        },
        display=Display(content=content),
        display_mode="optional",
    )


@tool(slash="메모 목록")
async def list_notes(ctx: ToolContext, game: GameRef | None = None) -> ToolResult:
    """내 메모 전체 목록을 표로 보여준다. 난이도가 빈 행은 곡 전체 메모다. 이 표로 delete_notes를 부를 수 있다."""
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT song_title, chart_type, difficulty, body, public, updated_at FROM notes_notes "
        "WHERE player_id = ? AND game = ? ORDER BY updated_at DESC",
        (ctx.player_id, ctx.game),
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    items: list[dict[str, Any]] = [
        {
            "song_title": r["song_title"], "chart_type": r["chart_type"], "difficulty": r["difficulty"] or None,
            "body": r["body"], "public": bool(r["public"]), "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    if not items:
        return ToolResult(data={"notes": []}, display=Display(content="메모가 없어."))

    builder = TableBuilder(subject=ctx.player_id, unit="메모")
    tc.add_columns(builder, *tc.identity_columns(ctx.game), game=ctx.game)
    builder.column(_N_BODY)
    builder.column(_N_PUBLIC)
    builder.column(_N_UPDATED, hidden_in_text=True)
    builder.row_formats(_NOTE_FORMATS)
    builder.step("내 메모 (최근 수정순)")
    typed = tc.CHART_TYPE in tc.identity_columns(ctx.game)
    for i in items:
        # 곡 전체 메모는 난이도가 비어 곡 단위 행이 된다.
        cells: dict[str, Cell] = {
            tc.SONG: tc.song_cell(i["song_title"]),
            tc.DIFFICULTY: tc.difficulty_cell(i["difficulty"]) if i["difficulty"] else EMPTY_CELL,
            _N_BODY: cell(i["body"]),
            _N_PUBLIC: cell("공개" if i["public"] else "비공개"),
            _N_UPDATED: cell(i["updated_at"]),
        }
        if typed:
            cells[tc.CHART_TYPE] = tc.text_cell(i["chart_type"]) if i["chart_type"] else EMPTY_CELL
        builder.row(
            RowRef(
                _NOTE, json.dumps([i["song_title"], i["chart_type"], i["difficulty"] or ""], ensure_ascii=False)
            ),
            cells,
        )
    return ToolResult(data={"notes": items}, table=builder.build())


_MY_NOTE = "내 메모"
_NO_NOTE = "메모 없음"


@tool()
async def add_notes(ctx: ToolContext, table: Annotated[TableRef, TableNeeds(nonempty=[tc.SONG])]) -> ToolResult:
    """곡·채보 표에 "내 메모" 칸을 더한 새 표를 돌려준다. 채보 행은 그 채보의 메모, 없으면 그 곡 전체 메모를
    쓰고, 곡 단위 행은 곡 전체 메모를 쓴다. 메모가 없으면 "메모 없음"이다."""
    assert isinstance(table, WorkTable)
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT song_title, chart_type, difficulty, body FROM notes_notes WHERE player_id = ? AND game = ?",
        (ctx.player_id, ctx.game),
    )
    bodies: dict[tuple[str, str, str], str] = {
        (r["song_title"], r["chart_type"], r["difficulty"] or ""): r["body"] for r in await cursor.fetchall()
    }

    def cells_for(row: Row) -> dict[str, Cell]:
        title, kind, code = _row_note_key(row)
        body = bodies.get((title, kind, code)) if code else None
        if body is None:
            body = bodies.get((title, "", ""))
        return {_MY_NOTE: cell(None, _NO_NOTE) if body is None else cell(body)}

    extended = extend_table(table, [Column(name=_MY_NOTE, label=_MY_NOTE)], cells_for, steps=["내 메모"])
    with_note = sum(1 for row in extended.rows if row.cell(_MY_NOTE).filled)
    return ToolResult(data={"count": extended.row_count, "with_note": with_note}, table=extended)


@tool(slash="메모 삭제", mutates=True, confirm="always")
async def delete(
    ctx: ToolContext, song: SongQuery, difficulty: DifficultyRef | None = None, chart_type: str | None = None,
    game: GameRef | None = None,
) -> ToolResult:
    """메모를 지운다. 본인 메모만 지울 수 있다. maimai는 같은 난이도에 STD/DX가 둘이면 chart_type을 준다."""
    assert isinstance(song, SongRecord)
    assert ctx.db is not None
    code = _difficulty_code(difficulty)
    try:
        kind = _chart_type_for(ctx, song, code, chart_type)
    except _NoteTargetError as exc:
        return ToolResult(error=str(exc))
    cursor = await ctx.db.execute(
        "DELETE FROM notes_notes "
        "WHERE player_id = ? AND game = ? AND song_title = ? AND chart_type = ? AND difficulty = ?",
        (ctx.player_id, ctx.game, song.title, kind, code),
    )
    await ctx.db.commit()
    if cursor.rowcount == 0:
        return ToolResult(error="그 메모를 찾지 못했어.")
    return ToolResult(
        data={"song_title": song.title, "difficulty": code, "chart_type": kind},
        display=Display(content="메모를 지웠어."),
        display_mode="optional",
    )


@tool(mutates=True, confirm="always")
async def delete_notes(ctx: ToolContext, table: Annotated[TableRef, TableNeeds(nonempty=[tc.SONG])]) -> ToolResult:
    """작업 표(list_notes 결과 등)의 행에 달린 내 메모를 일괄로 지운다. 채보 행은 그 채보의 메모만, 곡 단위 행은 곡
    전체 메모만 지운다(채보 행에 곡 전체 메모를 대신 지우지 않는다). 일부만 지우려면 table에 번호(예: "t3:2,4")나
    거른 표를 가리킨다. 본인 메모만 지우고 메모가 없는 행은 건너뛴다. 항상 확인 버튼을 받은 뒤에 지운다."""
    assert isinstance(table, WorkTable)
    assert ctx.db is not None
    keys = list(dict.fromkeys(_row_note_key(row) for row in table.rows))
    deleted: list[tuple[str, str, str]] = []
    for title, kind, code in keys:
        cursor = await ctx.db.execute(
            "DELETE FROM notes_notes "
            "WHERE player_id = ? AND game = ? AND song_title = ? AND chart_type = ? AND difficulty = ?",
            (ctx.player_id, ctx.game, title, kind, code),
        )
        if cursor.rowcount:
            deleted.append((title, kind, code))
    await ctx.db.commit()
    missing = len(keys) - len(deleted)
    if not deleted:
        return ToolResult(error="지울 메모가 없어." + (f" ({missing}개는 이미 없음)" if missing else ""))
    note = f" ({missing}개는 메모가 없어서 뺐어.)" if missing else ""
    return ToolResult(
        data={"deleted_count": len(deleted), "missing": missing, "notes": [_head(t, c, k) for t, k, c in deleted]},
        display=Display(content=f"메모 {len(deleted)}개를 지웠어.{note}"),
        display_mode="optional",
    )


def _row_note_key(row: Row) -> tuple[str, str, str]:
    """표 행이 가리키는 메모의 (곡, 채보 종류, 난이도 코드). 곡 단위 행(난이도 칸 비음)은 곡 전체 메모 키("", "")다."""
    difficulty = row.cell(tc.DIFFICULTY).raw
    if not difficulty:
        return str(row.cell(tc.SONG).raw), "", ""
    title, kind, code = tc.row_chart_key(row)
    return title, kind, code


# ---------------------------------------------------------------- 이벤트 핸들러


def _scope_label(difficulty: str, chart_type: str = "") -> str:
    return " ".join(part for part in (chart_type, difficulty) if part) or "곡 전체"


async def _fetch_viewed_notes(
    ctx: ToolContext, *, game: str, viewer_id: str, song_title: str, difficulty: str | None, chart_type: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """곡 조회 조각에 쓸 메모. difficulty가 None(곡 전체 보기)이면 그 곡의 모든 채보 메모를, 난이도 코드면 그
    채보(종류까지) 메모와 곡 전체 메모("")를 모은다. (내 메모, 보여줄 남의 공개 메모, 남의 공개 메모 전체 개수)."""
    assert ctx.db is not None
    scope_sql = "" if difficulty is None else " AND ((chart_type = ? AND difficulty = ?) OR difficulty = '')"
    scope_params: tuple[str, ...] = () if difficulty is None else (chart_type or "", difficulty)
    cursor = await ctx.db.execute(
        "SELECT player_id, chart_type, difficulty, body, public, updated_at FROM notes_notes "
        f"WHERE game = ? AND song_title = ?{scope_sql} AND (player_id = ? OR public = 1) "
        "ORDER BY updated_at DESC",
        (game, song_title, *scope_params, viewer_id),
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    mine = [
        {"chart_type": r["chart_type"] or None, "difficulty": r["difficulty"] or None, **_note_dict(r)}
        for r in rows if r["player_id"] == viewer_id
    ]
    others_all = [
        {"player_id": r["player_id"], "chart_type": r["chart_type"] or None, "difficulty": r["difficulty"] or None,
         "body": r["body"], "updated_at": r["updated_at"]}
        for r in rows
        if r["player_id"] != viewer_id
    ]
    return mine, others_all[:_OTHERS_LIMIT], len(others_all)


async def _song_viewed(ctx: ToolContext, payload: dict[str, Any], game: str) -> Section | None:
    player_id: str = payload["player_id"]
    title: str = payload["song_title"]
    code: str | None = payload.get("difficulty") or None
    kind: str | None = payload.get("chart_type") or None

    mine, others, others_total = await _fetch_viewed_notes(
        ctx, game=game, viewer_id=player_id, song_title=title, difficulty=code, chart_type=kind
    )
    if not mine and not others:
        return None

    lines = [f"내 메모 ({_scope_label(m['difficulty'] or '', m['chart_type'] or '')})\n{m['body']}" for m in mine]
    lines += [
        f"{o['player_id']}의 메모 ({_scope_label(o['difficulty'] or '', o['chart_type'] or '')})\n{o['body']}"
        for o in others
    ]
    if others_total > len(others):
        lines.append(f"...외 {others_total - len(others)}개 더 있음")

    return Section(
        order=_SONG_VIEW_ORDER,
        display=Display(content="\n\n".join([_head(title, code or "", kind or ""), *lines])),
        data={
            "kind": "song_notes", "difficulty": code, "chart_type": kind, "mine": mine, "others": others,
            "others_total": others_total, "has_more": others_total > len(others),
        },
    )


@on("chunithm.song.viewed")
async def on_song_viewed(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """그 곡에 내 메모나 공개된 남의 메모가 있으면 조각으로 돌려준다. difficulty가 None이면(곡
    전체 보기) 모든 난이도의 메모를, 난이도 코드면(채보 보기) 그 난이도와 곡 전체 메모를 모은다.
    둘 다 없으면 None(모으기에서 조각이 빠진다)."""
    return await _song_viewed(ctx, payload, "chunithm")


@on("maimai.song.viewed")
async def on_maimai_song_viewed(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """`on_song_viewed`의 maimai 판. 채보 종류(STD/DX)까지 맞는 메모와 곡 전체 메모를 모은다."""
    return await _song_viewed(ctx, payload, "maimai")


@on("chunithm.plan.requested")
async def on_plan_requested(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """내 메모 중 최근 것 몇 개를 보여준다(페이로드에 후보곡 목록이 없어 후보로 거르지
    않는다). 메모가 없으면 None."""
    assert ctx.db is not None
    player_id: str = payload["player_id"]
    cursor = await ctx.db.execute(
        "SELECT song_title, difficulty, body, updated_at FROM notes_notes "
        "WHERE player_id = ? AND game = 'chunithm' ORDER BY updated_at DESC LIMIT ?",
        (player_id, _PLAN_LIMIT),
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    if not rows:
        return None

    items: list[dict[str, Any]] = [
        {"song_title": r["song_title"], "difficulty": r["difficulty"] or None, "body": r["body"]} for r in rows
    ]
    lines = [f"{_head(i['song_title'], i['difficulty'] or '')}  {i['body']}" for i in items]
    return Section(
        order=_PLAN_ORDER,
        display=Display(content="\n".join(lines)),
        data={"kind": "plan_notes", "notes": items},
    )
