# skills/folders/skill.py
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import aiosqlite

from core.decorators import tool
from core.tables import RowRef, TableBuilder, TableNeeds, WorkTable, cell
from core.tool_types import (
    ChartRef,
    Display,
    GameRef,
    TableRef,
    ToolContext,
    ToolResult,
)
from services.game import table_columns as tc
from services.game.chart_ref import ResolvedChart

_FOLDER = "folders.folder"
_F_NAME = "이름"
_F_COUNT = "채보 수"
_F_NOTE = "메모 유무"
# 일괄 추가/삭제 결과를 말로 늘어놓을 때 자르는 개수. 조건에 따라 대상이 백 개 가까이
# 나올 수 있어(예: 특정 레벨 전체), 전부 쓰면 디스코드 메시지가 과하게 길어진다.
_DISPLAY_LIMIT = 15


async def _fetch_folder(ctx: ToolContext, name: str) -> aiosqlite.Row | None:
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT id, name, note, created_at, updated_at FROM folders_folders "
        "WHERE player_id = ? AND game = ? AND LOWER(name) = LOWER(?)",
        (ctx.player_id, ctx.game, name),
    )
    return await cursor.fetchone()


async def _folder_names(ctx: ToolContext) -> list[str]:
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT name FROM folders_folders WHERE player_id = ? AND game = ? ORDER BY name",
        (ctx.player_id, ctx.game),
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    return [r["name"] for r in rows]


async def _require_folder(ctx: ToolContext, name: str) -> aiosqlite.Row | ToolResult:
    folder = await _fetch_folder(ctx, name)
    if folder is not None:
        return folder
    existing = await _folder_names(ctx)
    hint = f" 지금 있는 폴더: {', '.join(existing)}" if existing else " 아직 폴더가 없어."
    return ToolResult(error=f"'{name}' 폴더를 찾지 못했어.{hint}")


async def _chart_count(ctx: ToolContext, folder_id: int) -> int:
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT COUNT(*) AS n FROM folders_charts WHERE folder_id = ?", (folder_id,)
    )
    row = await cursor.fetchone()
    assert row is not None
    return int(row["n"])


type _ChartKey = tuple[str, str, str]  # (곡, 채보 종류, 난이도 코드). 종류가 없는 게임은 가운데가 ""다.


def _label(key: _ChartKey) -> str:
    title, chart_type, difficulty = key
    return f"{title} {chart_type} {difficulty}" if chart_type else f"{title} {difficulty}"


async def _folder_chart_keys(ctx: ToolContext, folder_id: int) -> set[_ChartKey]:
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT song_title, chart_type, difficulty FROM folders_charts WHERE folder_id = ?", (folder_id,)
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    return {(r["song_title"], r["chart_type"], r["difficulty"]) for r in rows}


def _table_chart_keys(table: WorkTable) -> list[_ChartKey]:
    """표의 채보 행(곡·난이도 칸이 찬 행)의 (곡, 채보 종류, 난이도 코드). 같은 채보는 한 번만."""
    return list(dict.fromkeys(tc.row_chart_key(row) for row in table.rows))


def _display_names(names: list[str]) -> str:
    shown = names[:_DISPLAY_LIMIT]
    text = ", ".join(shown)
    if len(names) > len(shown):
        text += f" 외 {len(names) - len(shown)}개"
    return text


# ---------------------------------------------------------------- 폴더 자체


@tool(slash="폴더 생성", mutates=True)
async def create_folder(ctx: ToolContext, name: str, game: GameRef | None = None) -> ToolResult:
    """새 빈 폴더를 만든다. 이미 있는 이름(대소문자 구분 안 함)이면 에러다. 이름을 사용자가
    직접 말하지 않았으면, 뒤이어 어떤 조건으로 채울지(상수·레벨·버전 등)를 보고 그에 맞는
    이름을 지어 준다(예: "15.0 상수 곡 폴더 만들어줘" → "상수 15.0 폴더"). 맥락이 전혀 없으면
    이름을 물어본다."""
    assert ctx.db is not None
    existing = await _fetch_folder(ctx, name)
    if existing is not None:
        return ToolResult(error=f"'{name}' 폴더가 이미 있어.")
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        "INSERT INTO folders_folders (player_id, game, name, note, created_at, updated_at) "
        "VALUES (?, ?, ?, NULL, ?, ?)",
        (ctx.player_id, ctx.game, name, now, now),
    )
    await ctx.db.commit()
    return ToolResult(
        data={"name": name},
        display=Display(content=f"'{name}' 폴더를 만들었어."),
        display_mode="optional",
    )


@tool(slash="폴더 삭제", mutates=True, confirm="always")
async def delete_folder(ctx: ToolContext, name: str, game: GameRef | None = None) -> ToolResult:
    """폴더와 그 안의 채보 목록·메모를 통째로 지운다. 되돌릴 수 없다."""
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    await ctx.db.execute("DELETE FROM folders_charts WHERE folder_id = ?", (folder["id"],))
    await ctx.db.execute("DELETE FROM folders_folders WHERE id = ?", (folder["id"],))
    await ctx.db.commit()
    return ToolResult(
        data={"name": folder["name"]},
        display=Display(content=f"'{folder['name']}' 폴더를 지웠어."),
        display_mode="optional",
    )


@tool(slash="폴더 이름변경", mutates=True)
async def rename_folder(ctx: ToolContext, name: str, new_name: str, game: GameRef | None = None) -> ToolResult:
    """폴더 이름을 바꾼다. 새 이름이 이미 다른 폴더가 쓰고 있으면(대소문자 구분 안 함) 에러다."""
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    clash = await _fetch_folder(ctx, new_name)
    if clash is not None and clash["id"] != folder["id"]:
        return ToolResult(error=f"'{new_name}' 폴더가 이미 있어.")
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        "UPDATE folders_folders SET name = ?, updated_at = ? WHERE id = ?", (new_name, now, folder["id"])
    )
    await ctx.db.commit()
    return ToolResult(
        data={"old_name": folder["name"], "new_name": new_name},
        display=Display(content=f"'{folder['name']}'을(를) '{new_name}'(으)로 바꿨어."),
        display_mode="optional",
    )


@tool(slash="폴더 목록")
async def list_folders(ctx: ToolContext, game: GameRef | None = None) -> ToolResult:
    """내 폴더 목록(이름, 채보 수, 메모 유무)을 표로 보여준다."""
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT id, name, note FROM folders_folders WHERE player_id = ? AND game = ? ORDER BY name",
        (ctx.player_id, ctx.game),
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    if not rows:
        return ToolResult(data={"folders": []}, display=Display(content="폴더가 없어."))
    items: list[dict[str, Any]] = []
    builder = TableBuilder(subject=ctx.player_id, unit="폴더")
    builder.column(_F_NAME)
    builder.column(_F_COUNT, value_type="number")
    builder.column(_F_NOTE)
    builder.step("내 폴더 (이름순)")
    for row in rows:
        count = await _chart_count(ctx, row["id"])
        has_note = row["note"] is not None
        items.append({"name": row["name"], "chart_count": count, "has_note": has_note})
        builder.row(
            RowRef(_FOLDER, row["id"]),
            {_F_NAME: cell(row["name"]), _F_COUNT: cell(count), _F_NOTE: cell("있음" if has_note else "없음")},
        )
    return ToolResult(data={"folders": items}, table=builder.build())


@tool(slash="폴더 보기")
async def show_folder(ctx: ToolContext, name: str, game: GameRef | None = None) -> ToolResult:
    """폴더의 메모와 안에 든 채보 전체(곡, 난이도, 상수)를 표로 돌려준다."""
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    cursor = await ctx.db.execute(
        "SELECT song_title, chart_type, difficulty FROM folders_charts WHERE folder_id = ? "
        "ORDER BY song_title, chart_type, difficulty",
        (folder["id"],),
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    if not rows:
        lines = [folder["name"]]
        if folder["note"]:
            lines.append(f"메모: {folder['note']}")
        lines.append("채보가 없어.")
        return ToolResult(
            data={"name": folder["name"], "note": folder["note"], "count": 0},
            display=Display(content="\n".join(lines)),
        )

    identity = tc.identity_columns(ctx.game)
    columns = (*identity, tc.CONST)
    builder = TableBuilder(subject=ctx.player_id, unit="채보")
    tc.add_columns(builder, *columns, game=ctx.game).step(f"{folder['name']} 폴더")
    summary = {"폴더": folder["name"]}
    if folder["note"]:
        summary["메모"] = folder["note"]
    builder.summary(summary)
    for r in rows:
        title, chart_type, difficulty = r["song_title"], r["chart_type"], r["difficulty"]
        chart = ctx.songdb.chart_for(title, difficulty, chart_type)
        cells = {
            tc.SONG: tc.song_cell(title),
            tc.DIFFICULTY: tc.difficulty_cell(difficulty),
            tc.CONST: tc.const_cell(
                chart.const if chart is not None else None,
                estimated=chart is not None and chart.is_const_unknown,
            ),
        }
        if tc.CHART_TYPE in identity:
            cells[tc.CHART_TYPE] = tc.text_cell(chart_type)
        builder.row(tc.chart_ref(title, difficulty, chart_type), cells)
    return ToolResult(
        data={"name": folder["name"], "note": folder["note"], "count": len(rows)}, table=builder.build()
    )


@tool(mutates=True, confirm="bulk")
async def save_table(
    ctx: ToolContext,
    table: Annotated[TableRef, TableNeeds(nonempty=[tc.SONG, tc.DIFFICULTY])],
    name: str,
    *,
    dry_run: bool = False,
) -> ToolResult:
    """작업 표의 채보 행(곡·난이도가 있는 행)으로 새 폴더를 만든다. 같은 이름의 폴더가 이미 있으면 에러다.
    표의 칸은 저장하지 않고 채보 목록만 담는다. 이미 있는 폴더에 더하는 도구가 아니다.
    채보가 2개 이상이면 실행기가 개수를 보여주고 확인을 받는다."""
    assert isinstance(table, WorkTable)
    assert ctx.db is not None
    if await _fetch_folder(ctx, name) is not None:
        return ToolResult(error=f"'{name}' 폴더가 이미 있어.")

    keys = _table_chart_keys(table)
    labels = [_label(key) for key in keys]
    notes = [step for step in table.steps if "뺌" in step]

    if dry_run:
        return ToolResult(
            data={"count": len(keys), "charts": labels, "notes": notes},
            display=Display(content=f"'{name}' 폴더를 만들고 {len(keys)}개를 담을게: {_display_names(labels)}"),
        )

    now = datetime.now(UTC).isoformat()
    cursor = await ctx.db.execute(
        "INSERT INTO folders_folders (player_id, game, name, note, created_at, updated_at) "
        "VALUES (?, ?, ?, NULL, ?, ?)",
        (ctx.player_id, ctx.game, name, now, now),
    )
    folder_id = cursor.lastrowid
    for title, chart_type, difficulty in keys:
        await ctx.db.execute(
            "INSERT INTO folders_charts (folder_id, song_title, chart_type, difficulty, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (folder_id, title, chart_type, difficulty, now),
        )
    await ctx.db.commit()
    return ToolResult(
        data={"name": name, "count": len(keys), "notes": notes},
        display=Display(content=f"'{name}' 폴더를 만들고 {len(keys)}개를 담았어: {_display_names(labels)}"),
    )


# ---------------------------------------------------------------- 채보 한 개씩


@tool(slash="폴더 채보추가", mutates=True)
async def add_chart(ctx: ToolContext, name: str, chart: ChartRef, game: GameRef | None = None) -> ToolResult:
    """폴더에 채보 하나를 추가한다. 이미 있으면 아무것도 안 하고 그 사실만 알려준다."""
    assert isinstance(chart, ResolvedChart)
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    key = (chart.song.title, chart.chart.chart_type, chart.chart.difficulty)
    if key in await _folder_chart_keys(ctx, folder["id"]):
        return ToolResult(
            data={"name": folder["name"], "added": False},
            display=Display(content=f"'{folder['name']}'에 이미 있는 채보야."),
            display_mode="optional",
        )
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        "INSERT INTO folders_charts (folder_id, song_title, chart_type, difficulty, added_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (folder["id"], *key, now),
    )
    await ctx.db.execute("UPDATE folders_folders SET updated_at = ? WHERE id = ?", (now, folder["id"]))
    await ctx.db.commit()
    return ToolResult(
        data={"name": folder["name"], "added": True},
        display=Display(
            content=f"'{folder['name']}'에 {_label(key)}를 추가했어."
        ),
        display_mode="optional",
    )


@tool(slash="폴더 채보삭제", mutates=True)
async def remove_chart(ctx: ToolContext, name: str, chart: ChartRef, game: GameRef | None = None) -> ToolResult:
    """폴더에서 채보 하나를 뺀다. 없으면 아무것도 안 하고 그 사실만 알려준다."""
    assert isinstance(chart, ResolvedChart)
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    cursor = await ctx.db.execute(
        "DELETE FROM folders_charts WHERE folder_id = ? AND song_title = ? AND chart_type = ? AND difficulty = ?",
        (folder["id"], chart.song.title, chart.chart.chart_type, chart.chart.difficulty),
    )
    if cursor.rowcount == 0:
        return ToolResult(
            data={"name": folder["name"], "removed": False},
            display=Display(content=f"'{folder['name']}'에 그 채보가 없어."),
            display_mode="optional",
        )
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute("UPDATE folders_folders SET updated_at = ? WHERE id = ?", (now, folder["id"]))
    await ctx.db.commit()
    return ToolResult(
        data={"name": folder["name"], "removed": True},
        display=Display(
            content=f"'{folder['name']}'에서 {_label((chart.song.title, chart.chart.chart_type, chart.chart.difficulty))}를 뺐어."
        ),
        display_mode="optional",
    )


# ---------------------------------------------------------------- 표로 일괄


_NEEDS_CHARTS = TableNeeds(nonempty=[tc.SONG, tc.DIFFICULTY])


@tool(mutates=True, confirm="bulk")
async def add_to_folder(
    ctx: ToolContext, name: str, table: Annotated[TableRef, _NEEDS_CHARTS], *, dry_run: bool = False
) -> ToolResult:
    """이미 있는 폴더에 작업 표의 채보 행(곡·난이도가 있는 행)을 일괄 추가한다. 이미 폴더에 있는 채보는 뺀다.
    새 폴더를 만들 때는 save_table을 쓴다. 대상이 2개 이상이면 실행기가 먼저 개수를 보여주고 확인을 받는다."""
    assert isinstance(table, WorkTable)
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    keys = _table_chart_keys(table)
    existing = await _folder_chart_keys(ctx, folder["id"])
    targets = [key for key in keys if key not in existing]
    already = len(keys) - len(targets)
    names = [_label(key) for key in targets]
    notes = _skip_notes(table, already, "이미 폴더에 있음")

    if not targets:
        return ToolResult(
            data={"count": 0, "charts": [], "already": already, "notes": notes},
            display=Display(content="표의 채보가 전부 이미 폴더에 있어."),
            display_mode="required",
        )
    if dry_run:
        return ToolResult(
            data={"count": len(targets), "charts": names, "already": already, "notes": notes},
            display=Display(
                content=f"'{folder['name']}'에 {len(targets)}개를 추가할게: {_display_names(names)}{_notes_text(notes)}"
            ),
            display_mode="required",
        )

    now = datetime.now(UTC).isoformat()
    for title, chart_type, difficulty in targets:
        await ctx.db.execute(
            "INSERT INTO folders_charts (folder_id, song_title, chart_type, difficulty, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (folder["id"], title, chart_type, difficulty, now),
        )
    await ctx.db.execute("UPDATE folders_folders SET updated_at = ? WHERE id = ?", (now, folder["id"]))
    await ctx.db.commit()
    return ToolResult(
        data={"count": len(targets), "charts": names, "already": already, "notes": notes},
        display=Display(
            content=f"'{folder['name']}'에 {len(targets)}개를 추가했어: {_display_names(names)}{_notes_text(notes)}"
        ),
        display_mode="required",
    )


@tool(mutates=True, confirm="bulk")
async def remove_from_folder(
    ctx: ToolContext, name: str, table: Annotated[TableRef, _NEEDS_CHARTS], *, dry_run: bool = False
) -> ToolResult:
    """폴더에서 작업 표의 채보 행(곡·난이도가 있는 행) 중 지금 폴더에 들어 있는 것만 일괄로 뺀다. 폴더 자체를
    지우는 도구가 아니다. 대상이 2개 이상이면 실행기가 먼저 개수를 보여주고 확인을 받는다."""
    assert isinstance(table, WorkTable)
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    keys = _table_chart_keys(table)
    existing = await _folder_chart_keys(ctx, folder["id"])
    targets = [key for key in keys if key in existing]
    missing = len(keys) - len(targets)
    names = [_label(key) for key in targets]
    notes = _skip_notes(table, missing, "폴더에 없음")

    if not targets:
        return ToolResult(
            data={"count": 0, "charts": [], "missing": missing, "notes": notes},
            display=Display(content="표의 채보가 폴더에 하나도 없어."),
            display_mode="required",
        )
    if dry_run:
        return ToolResult(
            data={"count": len(targets), "charts": names, "missing": missing, "notes": notes},
            display=Display(
                content=f"'{folder['name']}'에서 {len(targets)}개를 뺄게: {_display_names(names)}{_notes_text(notes)}"
            ),
            display_mode="required",
        )

    now = datetime.now(UTC).isoformat()
    for title, chart_type, difficulty in targets:
        await ctx.db.execute(
            "DELETE FROM folders_charts WHERE folder_id = ? AND song_title = ? AND chart_type = ? AND difficulty = ?",
            (folder["id"], title, chart_type, difficulty),
        )
    await ctx.db.execute("UPDATE folders_folders SET updated_at = ? WHERE id = ?", (now, folder["id"]))
    await ctx.db.commit()
    return ToolResult(
        data={"count": len(targets), "charts": names, "missing": missing, "notes": notes},
        display=Display(
            content=f"'{folder['name']}'에서 {len(targets)}개를 뺐어: {_display_names(names)}{_notes_text(notes)}"
        ),
        display_mode="required",
    )


def _skip_notes(table: WorkTable, skipped: int, reason: str) -> list[str]:
    """표 과정의 "뺌" 안내와 이 도구가 지금 DB를 보고 뺀 개수 안내."""
    notes = [step for step in table.steps if "뺌" in step]
    if skipped:
        notes.append(f"{skipped}개는 {reason}")
    return notes


def _notes_text(notes: list[str]) -> str:
    return f" ({'; '.join(notes)})" if notes else ""


# ---------------------------------------------------------------- 메모


@tool(slash="폴더 메모", mutates=True)
async def set_note(
    ctx: ToolContext, name: str, text: str, append: bool = False, game: GameRef | None = None
) -> ToolResult:
    """폴더에 메모를 남긴다. 같은 폴더에 다시 쓰면 덮어쓰고, append=True면 줄바꿈으로
    이어 붙인다. 빈 문자열을 주면 메모를 지운다."""
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    body = f"{folder['note']}\n{text}" if append and folder["note"] else text
    body = body or None
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        "UPDATE folders_folders SET note = ?, updated_at = ? WHERE id = ?", (body, now, folder["id"])
    )
    await ctx.db.commit()
    content = f"'{folder['name']}' 메모를 지웠어." if body is None else f"'{folder['name']}' 메모를 저장했어."
    return ToolResult(
        data={"name": folder["name"], "note": body},
        display=Display(content=content),
        display_mode="optional",
    )
