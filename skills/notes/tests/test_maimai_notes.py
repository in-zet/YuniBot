# skills/notes/tests/test_maimai_notes.py
from __future__ import annotations

import importlib.util
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
from core.tables import TableBuilder, render_text
from core.tool_types import ToolContext
from services.game import table_columns as tc
from services.game.terms import ResolvedDifficulty
from services.maimai.songdb import MaimaiSongDbService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_NOTES_SQL = Path(__file__).parent.parent / "migrations" / "001_init.sql"

_SAMPLE = [
    {
        "title": "Both", "artist": "A", "version": "20000", "image_url": "b.png", "intl": "1",
        "lev_mas": "12+", "lev_mas_i": "12.6", "lev_mas_notes": "346",
        "dx_lev_mas": "13+", "dx_lev_mas_i": "13.7", "dx_lev_mas_notes": "631",
    },
    {
        "title": "DX Only", "artist": "A", "version": "26000", "image_url": "d.png", "intl": "1",
        "dx_lev_mas": "14", "dx_lev_mas_i": "14.0", "dx_lev_mas_notes": "800",
    },
]


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_maimai_notes_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


@pytest.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_NOTES_SQL.read_text())
    await conn.executescript(_NOTES_SQL.with_name("002_chart_type.sql").read_text())
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def songdb(tmp_path: Path) -> MaimaiSongDbService:
    path = tmp_path / "music-ex-intl.json"
    path.write_text(json.dumps(_SAMPLE, ensure_ascii=False), encoding="utf-8")
    return MaimaiSongDbService(path)


def _ctx(db: aiosqlite.Connection, songdb: MaimaiSongDbService, *, player_id: str = "p1", game: str = "maimai") -> ToolContext:
    return ToolContext(
        player_id=player_id, tags=frozenset(), services={"songdb": songdb}, db=db, event_bus=EventBus(), game=game
    )


def _mas() -> ResolvedDifficulty:
    return ResolvedDifficulty(canonical="MASTER")


async def _rows(db: aiosqlite.Connection) -> list[tuple[Any, ...]]:
    cursor = await db.execute("SELECT game, song_title, chart_type, difficulty, body FROM notes_notes ORDER BY 1,2,3,4")
    return [tuple(r) for r in await cursor.fetchall()]


async def test_note_on_a_song_with_both_chart_types_needs_the_type(skill, db, songdb) -> None:
    ctx = _ctx(db, songdb)
    song = songdb.song_by_title("Both")

    ambiguous = await skill.set_note(ctx, song, "메모", difficulty=_mas())
    typed = await skill.set_note(ctx, song, "DX 쪽 메모", difficulty=_mas(), chart_type="dx")

    assert ambiguous.error is not None and "STD / DX" in ambiguous.error
    assert typed.error is None and typed.data["chart_type"] == "DX"
    assert await _rows(db) == [("maimai", "Both", "DX", "MAS", "DX 쪽 메모")]


async def test_single_chart_type_is_inferred_and_wrong_type_is_rejected(skill, db, songdb) -> None:
    ctx = _ctx(db, songdb)
    song = songdb.song_by_title("DX Only")

    inferred = await skill.set_note(ctx, song, "추정", difficulty=_mas())
    wrong = await skill.set_note(ctx, song, "틀림", difficulty=_mas(), chart_type="STD")
    unknown = await skill.set_note(ctx, song, "틀림", difficulty=_mas(), chart_type="XX")

    assert inferred.error is None and inferred.data["chart_type"] == "DX"
    assert wrong.error is not None and "STD MAS 채보가 없어" in wrong.error
    assert unknown.error is not None and "STD, DX" in unknown.error


async def test_whole_song_note_has_no_chart_type_and_notes_of_both_types_coexist(skill, db, songdb) -> None:
    ctx = _ctx(db, songdb)
    song = songdb.song_by_title("Both")

    await skill.set_note(ctx, song, "곡 전체")
    await skill.set_note(ctx, song, "STD", difficulty=_mas(), chart_type="STD")
    await skill.set_note(ctx, song, "DX", difficulty=_mas(), chart_type="DX")

    assert await _rows(db) == [
        ("maimai", "Both", "", "", "곡 전체"),
        ("maimai", "Both", "DX", "MAS", "DX"),
        ("maimai", "Both", "STD", "MAS", "STD"),
    ]
    got = await skill.get_note(ctx, song, difficulty=_mas(), chart_type="DX")
    assert got.data["mine"]["body"] == "DX"


async def test_notes_are_per_game(skill, db, songdb) -> None:
    await skill.set_note(_ctx(db, songdb), songdb.song_by_title("DX Only"), "마이마이 메모")
    await db.execute(
        "INSERT INTO notes_notes (player_id, game, song_title, chart_type, difficulty, body, public, updated_at) "
        "VALUES ('p1', 'chunithm', 'DX Only', '', '', '츄니즘 메모', 0, 't')"
    )

    listed = await skill.list_notes(_ctx(db, songdb))

    assert [n["body"] for n in listed.data["notes"]] == ["마이마이 메모"]


def _table(songdb: MaimaiSongDbService, keys: list[tuple[str, str, str]]):
    """(제목, 채보 종류, 난이도) 행들로 maimai 채보 표를 만든다. 난이도가 ""이면 곡 단위 행이다."""
    builder = TableBuilder(unit="채보")
    tc.add_columns(builder, *tc.identity_columns("maimai"), game="maimai")
    for title, kind, code in keys:
        song = songdb.song_by_title(title)
        assert song is not None
        if code:
            chart = song.charts[f"{kind}:{code}"]
            builder.row(tc.chart_ref(title, code, kind), tc.chart_cells(song, chart, tc.identity_columns("maimai")))
        else:
            builder.row(tc.song_ref(title), tc.song_cells(song, tc.identity_columns("maimai")))
    return builder.build()


async def test_list_notes_table_has_type_column(skill, db, songdb) -> None:
    ctx = _ctx(db, songdb)
    song = songdb.song_by_title("Both")
    await skill.set_note(ctx, song, "STD", difficulty=_mas(), chart_type="STD")
    await skill.set_note(ctx, song, "곡 전체")

    listed = await skill.list_notes(ctx)

    table = listed.table
    assert table is not None
    assert [c.name for c in table.columns][:3] == [tc.SONG, tc.CHART_TYPE, tc.DIFFICULTY]
    assert "Both STD MAS" in render_text(table).content


async def test_delete_notes_removes_only_the_chart_type_of_the_row(skill, db, songdb) -> None:
    ctx = _ctx(db, songdb)
    song = songdb.song_by_title("Both")
    await skill.set_note(ctx, song, "STD", difficulty=_mas(), chart_type="STD")
    await skill.set_note(ctx, song, "DX", difficulty=_mas(), chart_type="DX")
    await skill.set_note(ctx, song, "곡 전체")

    deleted = await skill.delete_notes(ctx, table=_table(songdb, [("Both", "STD", "MAS"), ("Both", "", "")]))

    assert deleted.data["deleted_count"] == 2
    assert [(r[2], r[3]) for r in await _rows(db)] == [("DX", "MAS")]


async def test_add_notes_matches_the_chart_type_and_falls_back_to_the_song_note(skill, db, songdb) -> None:
    ctx = _ctx(db, songdb)
    song = songdb.song_by_title("Both")
    await skill.set_note(ctx, song, "곡 전체")
    await skill.set_note(ctx, song, "DX 전용", difficulty=_mas(), chart_type="DX")

    result = await skill.add_notes(ctx, table=_table(songdb, [("Both", "DX", "MAS"), ("Both", "STD", "MAS")]))

    bodies = [row.cell("내 메모").text for row in result.table.rows]
    assert bodies == ["DX 전용", "곡 전체"]


async def test_song_viewed_section_shows_notes_of_that_chart_type_only(skill, db, songdb) -> None:
    ctx = _ctx(db, songdb)
    song = songdb.song_by_title("Both")
    await skill.set_note(ctx, song, "STD 메모", difficulty=_mas(), chart_type="STD")
    await skill.set_note(ctx, song, "DX 메모", difficulty=_mas(), chart_type="DX")

    section = await skill.on_maimai_song_viewed(
        ctx, {"player_id": "p1", "song_title": "Both", "difficulty": "MAS", "chart_type": "DX"}
    )
    whole = await skill.on_maimai_song_viewed(
        ctx, {"player_id": "p1", "song_title": "Both", "difficulty": None, "chart_type": None}
    )
    other_game = await skill.on_song_viewed(ctx, {"player_id": "p1", "song_title": "Both", "difficulty": None})

    assert section is not None and section.display is not None
    assert "DX 메모" in (section.display.content or "") and "STD 메모" not in (section.display.content or "")
    assert whole is not None and len(whole.data["mine"]) == 2
    assert other_game is None  # 츄니즘 메모는 없다
