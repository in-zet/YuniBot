# skills/chunithm-folders/skill.py
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import aiosqlite

from core.decorators import tool
from core.tables import TableBuilder, TableNeeds, WorkTable
from core.tool_types import (
    ChartRef,
    DifficultyRef,
    Display,
    TableRef,
    ToolContext,
    ToolResult,
)
from services.chunithm import table_columns as tc
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.songdb import LONG_TO_SHORT_DIFFICULTY, ConstRangeHit
from services.chunithm.terms import ResolvedDifficulty

_GAME = "chunithm"
# 일괄 추가/삭제 결과를 말로 늘어놓을 때 자르는 개수. 조건에 따라 대상이 백 개 가까이
# 나올 수 있어(예: 특정 레벨 전체), 전부 쓰면 디스코드 메시지가 과하게 길어진다.
_DISPLAY_LIMIT = 15


def _difficulty_code(difficulty: DifficultyRef | None) -> str | None:
    if difficulty is None:
        return None
    assert isinstance(difficulty, ResolvedDifficulty)
    return LONG_TO_SHORT_DIFFICULTY.get(difficulty.canonical, difficulty.canonical)


async def _fetch_folder(ctx: ToolContext, name: str) -> aiosqlite.Row | None:
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT id, name, note, created_at, updated_at FROM folders_folders "
        "WHERE player_id = ? AND game = ? AND LOWER(name) = LOWER(?)",
        (ctx.player_id, _GAME, name),
    )
    return await cursor.fetchone()


async def _folder_names(ctx: ToolContext) -> list[str]:
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT name FROM folders_folders WHERE player_id = ? AND game = ? ORDER BY name",
        (ctx.player_id, _GAME),
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


async def _folder_chart_keys(ctx: ToolContext, folder_id: int) -> set[tuple[str, str]]:
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT song_title, difficulty FROM folders_charts WHERE folder_id = ?", (folder_id,)
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    return {(r["song_title"], r["difficulty"]) for r in rows}


def _hits_for_condition(
    ctx: ToolContext,
    *,
    difficulty_code: str | None,
    min_const: float | None,
    max_const: float | None,
    level: str | None,
    version: str | None,
) -> list[ConstRangeHit] | str:
    """상수 범위·레벨·수록 버전 중 정확히 하나로 채보를 찾는다. 조건이 0개거나 2개 이상이면
    에러 문구를 돌려준다(목표 스킬의 "폴더 정의"와 같은 제약 — songdb에 장르가 없어
    장르 조합은 지원하지 않는다)."""
    given = [min_const is not None or max_const is not None, level is not None, version is not None]
    if sum(given) != 1:
        return "상수 범위(min_const·max_const)·레벨(level)·수록 버전(version) 중 하나만 줘."
    if given[0]:
        if min_const is None or max_const is None:
            return "상수 범위는 min_const·max_const를 같이 줘야 해."
        return ctx.songdb.find_by_const(min_const=min_const, max_const=max_const, difficulty=difficulty_code)
    if level is not None:
        return ctx.songdb.find_by_level(level=level, difficulty=difficulty_code)
    assert version is not None
    return ctx.songdb.find_by_version(version=version, difficulty=difficulty_code)


def _display_names(names: list[str]) -> str:
    shown = names[:_DISPLAY_LIMIT]
    text = ", ".join(shown)
    if len(names) > len(shown):
        text += f" 외 {len(names) - len(shown)}개"
    return text


# ---------------------------------------------------------------- 폴더 자체


@tool(slash="폴더 생성", mutates=True)
async def create_folder(ctx: ToolContext, name: str) -> ToolResult:
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
        (ctx.player_id, _GAME, name, now, now),
    )
    await ctx.db.commit()
    return ToolResult(
        data={"name": name},
        display=Display(content=f"'{name}' 폴더를 만들었어."),
        display_mode="optional",
    )


@tool(slash="폴더 삭제", mutates=True, confirm="always")
async def delete_folder(ctx: ToolContext, name: str) -> ToolResult:
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
async def rename_folder(ctx: ToolContext, name: str, new_name: str) -> ToolResult:
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
async def list_folders(ctx: ToolContext) -> ToolResult:
    """내 폴더 목록(이름, 채보 수, 메모 유무)을 보여준다."""
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT id, name, note FROM folders_folders WHERE player_id = ? AND game = ? ORDER BY name",
        (ctx.player_id, _GAME),
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())
    items: list[dict[str, Any]] = []
    for row in rows:
        count = await _chart_count(ctx, row["id"])
        items.append({"name": row["name"], "chart_count": count, "has_note": row["note"] is not None})

    def _line(i: dict[str, Any]) -> str:
        suffix = " (메모 있음)" if i["has_note"] else ""
        return f"{i['name']}  채보 {i['chart_count']}개{suffix}"

    lines = [_line(i) for i in items]
    return ToolResult(
        data={"folders": items},
        display=Display(content="\n".join(lines) if lines else "폴더가 없어.", omittable=True),
        display_mode="required",
    )


@tool(slash="폴더 보기")
async def show_folder(ctx: ToolContext, name: str) -> ToolResult:
    """폴더의 메모와 안에 든 채보 전체(곡, 난이도, 상수)를 표로 돌려준다."""
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    cursor = await ctx.db.execute(
        "SELECT song_title, difficulty FROM folders_charts WHERE folder_id = ? "
        "ORDER BY song_title, difficulty",
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
            display=Display(content="\n".join(lines), omittable=True),
        )

    columns = (tc.SONG, tc.DIFFICULTY, tc.CONST)
    builder = TableBuilder(subject=ctx.player_id, unit="채보")
    tc.add_columns(builder, *columns).step(f"{folder['name']} 폴더")
    summary = {"폴더": folder["name"]}
    if folder["note"]:
        summary["메모"] = folder["note"]
    builder.summary(summary)
    for r in rows:
        title, difficulty = r["song_title"], r["difficulty"]
        chart = ctx.songdb.chart_for(title, difficulty)
        builder.row(
            tc.chart_ref(title, difficulty),
            {
                tc.SONG: tc.song_cell(title),
                tc.DIFFICULTY: tc.difficulty_cell(difficulty),
                tc.CONST: tc.const_cell(
                    chart.const if chart is not None else None,
                    estimated=chart is not None and chart.is_const_unknown,
                ),
            },
        )
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

    keys: list[tuple[str, str]] = []
    for row in table.rows:
        difficulty = str(row.cell(tc.DIFFICULTY).raw)
        keys.append((str(row.cell(tc.SONG).raw), LONG_TO_SHORT_DIFFICULTY.get(difficulty, difficulty)))
    keys = list(dict.fromkeys(keys))
    labels = [f"{title} {difficulty}" for title, difficulty in keys]
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
        (ctx.player_id, _GAME, name, now, now),
    )
    folder_id = cursor.lastrowid
    for title, difficulty in keys:
        await ctx.db.execute(
            "INSERT INTO folders_charts (folder_id, song_title, difficulty, added_at) VALUES (?, ?, ?, ?)",
            (folder_id, title, difficulty, now),
        )
    await ctx.db.commit()
    return ToolResult(
        data={"name": name, "count": len(keys), "notes": notes},
        display=Display(content=f"'{name}' 폴더를 만들고 {len(keys)}개를 담았어: {_display_names(labels)}"),
    )


# ---------------------------------------------------------------- 채보 한 개씩


@tool(slash="폴더 채보추가", mutates=True)
async def add_chart(ctx: ToolContext, name: str, chart: ChartRef) -> ToolResult:
    """폴더에 채보 하나를 추가한다. 이미 있으면 아무것도 안 하고 그 사실만 알려준다."""
    assert isinstance(chart, ResolvedChart)
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    key = (chart.song.title, chart.chart.difficulty)
    if key in await _folder_chart_keys(ctx, folder["id"]):
        return ToolResult(
            data={"name": folder["name"], "added": False},
            display=Display(content=f"'{folder['name']}'에 이미 있는 채보야."),
            display_mode="optional",
        )
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        "INSERT INTO folders_charts (folder_id, song_title, difficulty, added_at) VALUES (?, ?, ?, ?)",
        (folder["id"], chart.song.title, chart.chart.difficulty, now),
    )
    await ctx.db.execute("UPDATE folders_folders SET updated_at = ? WHERE id = ?", (now, folder["id"]))
    await ctx.db.commit()
    return ToolResult(
        data={"name": folder["name"], "added": True},
        display=Display(
            content=f"'{folder['name']}'에 {chart.song.title} {chart.chart.difficulty}를 추가했어."
        ),
        display_mode="optional",
    )


@tool(slash="폴더 채보삭제", mutates=True)
async def remove_chart(ctx: ToolContext, name: str, chart: ChartRef) -> ToolResult:
    """폴더에서 채보 하나를 뺀다. 없으면 아무것도 안 하고 그 사실만 알려준다."""
    assert isinstance(chart, ResolvedChart)
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    cursor = await ctx.db.execute(
        "DELETE FROM folders_charts WHERE folder_id = ? AND song_title = ? AND difficulty = ?",
        (folder["id"], chart.song.title, chart.chart.difficulty),
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
            content=f"'{folder['name']}'에서 {chart.song.title} {chart.chart.difficulty}를 뺐어."
        ),
        display_mode="optional",
    )


# ---------------------------------------------------------------- 조건 일괄


@tool(slash="폴더 조건추가", mutates=True, confirm="bulk")
async def add_by_condition(
    ctx: ToolContext,
    name: str,
    difficulty: DifficultyRef | None = None,
    min_const: float | None = None,
    max_const: float | None = None,
    level: str | None = None,
    version: str | None = None,
    *,
    dry_run: bool = False,
) -> ToolResult:
    """상수 범위·레벨·수록 버전 중 하나(+ 선택적 난이도)에 맞는 채보를 찾아 폴더에 일괄
    추가한다. 이미 폴더에 있는 채보는 뺀다. 대상이 2개 이상이면 실행기가 먼저 개수를
    보여주고 확인을 받는다."""
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    hits = _hits_for_condition(
        ctx, difficulty_code=_difficulty_code(difficulty),
        min_const=min_const, max_const=max_const, level=level, version=version,
    )
    if isinstance(hits, str):
        return ToolResult(error=hits)

    existing = await _folder_chart_keys(ctx, folder["id"])
    targets = [hit for hit in hits if (hit.song.title, hit.chart.difficulty) not in existing]
    names = [f"{t.song.title} {t.chart.difficulty}" for t in targets]

    if not targets:
        return ToolResult(
            data={"count": 0, "charts": []},
            display=Display(content="그 조건에 맞는데 아직 없는 채보가 없어."),
            display_mode="required",
        )
    if dry_run:
        return ToolResult(
            data={"count": len(targets), "charts": names},
            display=Display(content=f"'{folder['name']}'에 {len(targets)}개를 추가할게: {_display_names(names)}"),
            display_mode="required",
        )

    now = datetime.now(UTC).isoformat()
    for t in targets:
        await ctx.db.execute(
            "INSERT INTO folders_charts (folder_id, song_title, difficulty, added_at) VALUES (?, ?, ?, ?)",
            (folder["id"], t.song.title, t.chart.difficulty, now),
        )
    await ctx.db.execute("UPDATE folders_folders SET updated_at = ? WHERE id = ?", (now, folder["id"]))
    await ctx.db.commit()
    return ToolResult(
        data={"count": len(targets), "charts": names},
        display=Display(content=f"'{folder['name']}'에 {len(targets)}개를 추가했어: {_display_names(names)}"),
        display_mode="required",
    )


@tool(slash="폴더 조건삭제", mutates=True, confirm="bulk")
async def remove_by_condition(
    ctx: ToolContext,
    name: str,
    difficulty: DifficultyRef | None = None,
    min_const: float | None = None,
    max_const: float | None = None,
    level: str | None = None,
    version: str | None = None,
    *,
    dry_run: bool = False,
) -> ToolResult:
    """상수 범위·레벨·수록 버전 중 하나(+ 선택적 난이도)에 맞는 채보 중 폴더에 지금 들어있는
    것만 일괄로 뺀다. 대상이 2개 이상이면 실행기가 먼저 개수를 보여주고 확인을 받는다."""
    assert ctx.db is not None
    folder = await _require_folder(ctx, name)
    if isinstance(folder, ToolResult):
        return folder
    hits = _hits_for_condition(
        ctx, difficulty_code=_difficulty_code(difficulty),
        min_const=min_const, max_const=max_const, level=level, version=version,
    )
    if isinstance(hits, str):
        return ToolResult(error=hits)

    existing = await _folder_chart_keys(ctx, folder["id"])
    targets = [hit for hit in hits if (hit.song.title, hit.chart.difficulty) in existing]
    names = [f"{t.song.title} {t.chart.difficulty}" for t in targets]

    if not targets:
        return ToolResult(
            data={"count": 0, "charts": []},
            display=Display(content="그 조건에 맞으면서 폴더에 있는 채보가 없어."),
            display_mode="required",
        )
    if dry_run:
        return ToolResult(
            data={"count": len(targets), "charts": names},
            display=Display(content=f"'{folder['name']}'에서 {len(targets)}개를 뺄게: {_display_names(names)}"),
            display_mode="required",
        )

    now = datetime.now(UTC).isoformat()
    for t in targets:
        await ctx.db.execute(
            "DELETE FROM folders_charts WHERE folder_id = ? AND song_title = ? AND difficulty = ?",
            (folder["id"], t.song.title, t.chart.difficulty),
        )
    await ctx.db.execute("UPDATE folders_folders SET updated_at = ? WHERE id = ?", (now, folder["id"]))
    await ctx.db.commit()
    return ToolResult(
        data={"count": len(targets), "charts": names},
        display=Display(content=f"'{folder['name']}'에서 {len(targets)}개를 뺐어: {_display_names(names)}"),
        display_mode="required",
    )


# ---------------------------------------------------------------- 메모


@tool(slash="폴더 메모", mutates=True)
async def set_note(ctx: ToolContext, name: str, text: str, append: bool = False) -> ToolResult:
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
