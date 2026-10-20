# skills/chunithm-notes/skill.py
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
    SongQuery,
    TableRef,
    ToolContext,
    ToolResult,
)
from services.chunithm import table_columns as tc
from services.chunithm.songdb import LONG_TO_SHORT_DIFFICULTY, SongRecord
from services.chunithm.terms import ResolvedDifficulty

_GAME = "chunithm"
# 남의 공개 메모를 한 번에 보여주는 개수(최근 것부터). 더 있으면 개수만 알려준다(더보기
# 버튼 자체는 다음 단계).
_OTHERS_LIMIT = 3
# chunithm.plan.requested 페이로드에는 후보곡 목록이 없다(결정 사항: Task 9/10/11 공통
# 전제, chunithm-goals의 on_plan_requested와 같은 전제) — 내 메모 중 최근 것 몇 개만 보여준다.
_PLAN_LIMIT = 5
_SONG_VIEW_ORDER = 20
_PLAN_ORDER = 20

_NOTE = "chunithm-notes.note"
_N_BODY = "메모"
_N_PUBLIC = "공개"
_N_UPDATED = "수정 시각"
_NOTE_FORMATS = ("{곡} {난이도}  ({공개})  {메모}", "{곡}  ({공개})  {메모}")


def _difficulty_code(difficulty: DifficultyRef | None) -> str:
    """난이도 인자를 저장용 짧은 코드로 바꾼다. 비어 있으면 곡 전체 메모(빈 문자열 — 결정
    사항 N: SQLite 복합 PK에서 NULL은 서로 다른 값으로 취급되어 덮어쓰기가 안 되므로 빈
    문자열을 쓴다)."""
    if difficulty is None:
        return ""
    assert isinstance(difficulty, ResolvedDifficulty)
    return LONG_TO_SHORT_DIFFICULTY.get(difficulty.canonical, difficulty.canonical)


def _note_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {"body": row["body"], "public": bool(row["public"]), "updated_at": row["updated_at"]}


async def _fetch_note(
    ctx: ToolContext, *, player_id: str, song_title: str, difficulty: str
) -> dict[str, Any] | None:
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT body, public, updated_at FROM notes_notes "
        "WHERE player_id = ? AND game = ? AND song_title = ? AND difficulty = ?",
        (player_id, _GAME, song_title, difficulty),
    )
    row = await cursor.fetchone()
    return None if row is None else _note_dict(row)


async def _fetch_public_others(
    ctx: ToolContext, *, song_title: str, difficulty: str, exclude_player_id: str, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """공개된 남의 메모를 최근 것부터 돌려준다. (보여줄 목록, 전체 개수)."""
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT player_id, body, updated_at FROM notes_notes "
        "WHERE game = ? AND song_title = ? AND difficulty = ? AND public = 1 AND player_id != ? "
        "ORDER BY updated_at DESC",
        (_GAME, song_title, difficulty, exclude_player_id),
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    total = len(rows)
    shown: list[dict[str, Any]] = [
        {"player_id": r["player_id"], "body": r["body"], "updated_at": r["updated_at"]} for r in rows[:limit]
    ]
    return shown, total


def _head(song_title: str, difficulty: str) -> str:
    return f"{song_title} {difficulty}" if difficulty else song_title


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
) -> ToolResult:
    """곡(또는 채보)에 메모를 쓴다. 난이도를 비우면 곡 전체 메모다. 같은 곳에 다시 쓰면
    덮어쓰고, append=True면 줄바꿈으로 이어 붙인다. public=True면 다른 사람도 곡 조회
    조각에서 볼 수 있다. public을 주지 않으면 이미 있는 메모는 공개 여부를 그대로 두고, 새
    메모는 비공개로 만든다. 본인 메모만 바꾼다."""
    assert isinstance(song, SongRecord)
    assert ctx.db is not None
    code = _difficulty_code(difficulty)
    existing = await _fetch_note(ctx, player_id=ctx.player_id, song_title=song.title, difficulty=code)
    body = f"{existing['body']}\n{text}" if append and existing is not None else text
    if public is None:
        public = bool(existing["public"]) if existing is not None else False
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        "INSERT INTO notes_notes (player_id, game, song_title, difficulty, body, public, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (player_id, game, song_title, difficulty) "
        "DO UPDATE SET body = excluded.body, public = excluded.public, updated_at = excluded.updated_at",
        (ctx.player_id, _GAME, song.title, code, body, int(public), now),
    )
    await ctx.db.commit()
    return ToolResult(
        data={"song_title": song.title, "difficulty": code, "body": body, "public": public},
        display=Display(content=f"{_head(song.title, code)} 메모를 저장했어."),
        display_mode="optional",
    )


@tool(slash="메모 보기")
async def get_note(ctx: ToolContext, song: SongQuery, difficulty: DifficultyRef | None = None) -> ToolResult:
    """내 메모와, 공개된 다른 사람의 메모(최근 3개 + 더 있으면 개수)를 함께 보여준다."""
    assert isinstance(song, SongRecord)
    code = _difficulty_code(difficulty)
    mine = await _fetch_note(ctx, player_id=ctx.player_id, song_title=song.title, difficulty=code)
    others, others_total = await _fetch_public_others(
        ctx, song_title=song.title, difficulty=code, exclude_player_id=ctx.player_id, limit=_OTHERS_LIMIT
    )

    head = _head(song.title, code)
    lines: list[str] = []
    if mine is not None:
        lines.append(f"내 메모\n{mine['body']}")
    lines += _others_lines(others, others_total)
    content = "\n\n".join([head, *lines]) if lines else f"{head}에 메모가 없어."

    return ToolResult(
        data={
            "song_title": song.title, "difficulty": code, "mine": mine, "others": others,
            "others_total": others_total, "has_more": others_total > len(others),
        },
        display=Display(content=content),
        display_mode="optional",
    )


@tool(slash="메모 목록")
async def list_notes(ctx: ToolContext) -> ToolResult:
    """내 메모 전체 목록을 표로 보여준다. 난이도가 빈 행은 곡 전체 메모다."""
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT song_title, difficulty, body, public, updated_at FROM notes_notes "
        "WHERE player_id = ? AND game = ? ORDER BY updated_at DESC",
        (ctx.player_id, _GAME),
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    items: list[dict[str, Any]] = [
        {
            "song_title": r["song_title"], "difficulty": r["difficulty"] or None,
            "body": r["body"], "public": bool(r["public"]), "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    if not items:
        return ToolResult(data={"notes": []}, display=Display(content="메모가 없어."))

    builder = TableBuilder(subject=ctx.player_id, unit="메모")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY)
    builder.column(_N_BODY)
    builder.column(_N_PUBLIC)
    builder.column(_N_UPDATED, hidden_in_text=True)
    builder.row_formats(_NOTE_FORMATS)
    builder.step("내 메모 (최근 수정순)")
    for i in items:
        # 곡 전체 메모는 난이도가 비어 곡 단위 행이 된다.
        builder.row(
            RowRef(_NOTE, json.dumps([i["song_title"], i["difficulty"] or ""], ensure_ascii=False)),
            {
                tc.SONG: tc.song_cell(i["song_title"]),
                tc.DIFFICULTY: tc.difficulty_cell(i["difficulty"]) if i["difficulty"] else EMPTY_CELL,
                _N_BODY: cell(i["body"]),
                _N_PUBLIC: cell("공개" if i["public"] else "비공개"),
                _N_UPDATED: cell(i["updated_at"]),
            },
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
        "SELECT song_title, difficulty, body FROM notes_notes WHERE player_id = ? AND game = ?",
        (ctx.player_id, _GAME),
    )
    bodies: dict[tuple[str, str], str] = {
        (r["song_title"], r["difficulty"] or ""): r["body"] for r in await cursor.fetchall()
    }

    def cells_for(row: Row) -> dict[str, Cell]:
        title = str(row.cell(tc.SONG).raw)
        difficulty = row.cell(tc.DIFFICULTY).raw
        code = LONG_TO_SHORT_DIFFICULTY.get(str(difficulty), str(difficulty)) if difficulty else ""
        body = bodies.get((title, code)) if code else None
        if body is None:
            body = bodies.get((title, ""))
        return {_MY_NOTE: cell(None, _NO_NOTE) if body is None else cell(body)}

    extended = extend_table(table, [Column(name=_MY_NOTE, label=_MY_NOTE)], cells_for, steps=["내 메모"])
    with_note = sum(1 for row in extended.rows if row.cell(_MY_NOTE).filled)
    return ToolResult(data={"count": extended.row_count, "with_note": with_note}, table=extended)


@tool(slash="메모 삭제", mutates=True, confirm="always")
async def delete(ctx: ToolContext, song: SongQuery, difficulty: DifficultyRef | None = None) -> ToolResult:
    """메모를 지운다. 본인 메모만 지울 수 있다."""
    assert isinstance(song, SongRecord)
    assert ctx.db is not None
    code = _difficulty_code(difficulty)
    cursor = await ctx.db.execute(
        "DELETE FROM notes_notes WHERE player_id = ? AND game = ? AND song_title = ? AND difficulty = ?",
        (ctx.player_id, _GAME, song.title, code),
    )
    await ctx.db.commit()
    if cursor.rowcount == 0:
        return ToolResult(error="그 메모를 찾지 못했어.")
    return ToolResult(
        data={"song_title": song.title, "difficulty": code},
        display=Display(content="메모를 지웠어."),
        display_mode="optional",
    )


# ---------------------------------------------------------------- 이벤트 핸들러


def _scope_label(difficulty: str) -> str:
    return difficulty or "곡 전체"


async def _fetch_viewed_notes(
    ctx: ToolContext, *, viewer_id: str, song_title: str, difficulty: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """곡 조회 조각에 쓸 메모. difficulty가 None(곡 전체 보기)이면 그 곡의 모든 난이도 메모를,
    난이도 코드면 그 난이도 메모와 곡 전체 메모("")를 모은다. (내 메모, 보여줄 남의 공개 메모,
    남의 공개 메모 전체 개수)."""
    assert ctx.db is not None
    scope_sql = "" if difficulty is None else " AND difficulty IN (?, '')"
    scope_params: tuple[str, ...] = () if difficulty is None else (difficulty,)
    cursor = await ctx.db.execute(
        "SELECT player_id, difficulty, body, public, updated_at FROM notes_notes "
        f"WHERE game = ? AND song_title = ?{scope_sql} AND (player_id = ? OR public = 1) "
        "ORDER BY updated_at DESC",
        (_GAME, song_title, *scope_params, viewer_id),
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    mine = [
        {"difficulty": r["difficulty"] or None, **_note_dict(r)} for r in rows if r["player_id"] == viewer_id
    ]
    others_all = [
        {"player_id": r["player_id"], "difficulty": r["difficulty"] or None, "body": r["body"],
         "updated_at": r["updated_at"]}
        for r in rows
        if r["player_id"] != viewer_id
    ]
    return mine, others_all[:_OTHERS_LIMIT], len(others_all)


@on("chunithm.song.viewed")
async def on_song_viewed(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """그 곡에 내 메모나 공개된 남의 메모가 있으면 조각으로 돌려준다. difficulty가 None이면(곡
    전체 보기) 모든 난이도의 메모를, 난이도 코드면(채보 보기) 그 난이도와 곡 전체 메모를 모은다.
    둘 다 없으면 None(모으기에서 조각이 빠진다)."""
    player_id: str = payload["player_id"]
    title: str = payload["song_title"]
    code: str | None = payload.get("difficulty") or None

    mine, others, others_total = await _fetch_viewed_notes(
        ctx, viewer_id=player_id, song_title=title, difficulty=code
    )
    if not mine and not others:
        return None

    lines = [f"내 메모 ({_scope_label(m['difficulty'] or '')})\n{m['body']}" for m in mine]
    lines += [f"{o['player_id']}의 메모 ({_scope_label(o['difficulty'] or '')})\n{o['body']}" for o in others]
    if others_total > len(others):
        lines.append(f"...외 {others_total - len(others)}개 더 있음")

    return Section(
        order=_SONG_VIEW_ORDER,
        display=Display(content="\n\n".join([_head(title, code or ""), *lines])),
        data={
            "kind": "song_notes", "difficulty": code, "mine": mine, "others": others,
            "others_total": others_total, "has_more": others_total > len(others),
        },
    )


@on("chunithm.plan.requested")
async def on_plan_requested(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """내 메모 중 최근 것 몇 개를 보여준다(페이로드에 후보곡 목록이 없어 후보로 거르지
    않는다). 메모가 없으면 None."""
    assert ctx.db is not None
    player_id: str = payload["player_id"]
    cursor = await ctx.db.execute(
        "SELECT song_title, difficulty, body, updated_at FROM notes_notes "
        "WHERE player_id = ? AND game = ? ORDER BY updated_at DESC LIMIT ?",
        (player_id, _GAME, _PLAN_LIMIT),
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
