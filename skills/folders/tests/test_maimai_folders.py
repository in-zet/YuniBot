# skills/folders/tests/test_maimai_folders.py
from __future__ import annotations

import importlib.util
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from types import ModuleType

import aiosqlite
import pytest

from core.events import EventBus
from core.tables import TableBuilder, render_text
from core.tool_types import ToolContext
from services.game import table_columns as tc
from services.game.chart_ref import ResolvedChart
from services.maimai.songdb import MaimaiSongDbService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_FOLDERS_SQL = Path(__file__).parent.parent / "migrations" / "001_init.sql"

_SAMPLE = [
    {
        "title": "Both", "artist": "A", "version": "20000", "image_url": "b.png", "intl": "1",
        "lev_mas": "12+", "lev_mas_i": "12.6", "lev_mas_notes": "346",
        "dx_lev_mas": "13+", "dx_lev_mas_i": "13.7", "dx_lev_mas_notes": "631",
    },
]


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_maimai_folders_skill_under_test", _SKILL_PY)
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
    await conn.executescript(_FOLDERS_SQL.read_text())
    await conn.executescript(_FOLDERS_SQL.with_name("002_chart_type.sql").read_text())
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def songdb(tmp_path: Path) -> MaimaiSongDbService:
    path = tmp_path / "music-ex-intl.json"
    path.write_text(json.dumps(_SAMPLE, ensure_ascii=False), encoding="utf-8")
    return MaimaiSongDbService(path)


def _ctx(db: aiosqlite.Connection, songdb: MaimaiSongDbService, game: str = "maimai") -> ToolContext:
    return ToolContext(
        player_id="p1", tags=frozenset(), services={"songdb": songdb}, db=db, event_bus=EventBus(), game=game
    )


def _chart(songdb: MaimaiSongDbService, key: str) -> ResolvedChart:
    song = songdb.song_by_title("Both")
    assert song is not None
    return ResolvedChart(song=song, chart=song.charts[key])


async def test_std_and_dx_of_the_same_song_are_separate_folder_entries(skill, db, songdb) -> None:
    ctx = _ctx(db, songdb)
    await skill.create_folder(ctx, "F")

    await skill.add_chart(ctx, "F", _chart(songdb, "STD:MAS"))
    await skill.add_chart(ctx, "F", _chart(songdb, "DX:MAS"))
    again = await skill.add_chart(ctx, "F", _chart(songdb, "DX:MAS"))

    assert again.data["added"] is False
    shown = await skill.show_folder(ctx, "F")
    table = shown.table
    assert table is not None
    assert [c.name for c in table.columns] == [tc.SONG, tc.CHART_TYPE, tc.DIFFICULTY, tc.CONST]
    assert [tc.row_chart_key(r) for r in table.rows] == [("Both", "DX", "MAS"), ("Both", "STD", "MAS")]
    assert [r.cell(tc.CONST).text for r in table.rows] == ["13.7", "12.6"]
    assert "1. Both  DX  MAS  상수 13.7" in render_text(table).content


async def test_remove_chart_only_removes_the_given_chart_type(skill, db, songdb) -> None:
    ctx = _ctx(db, songdb)
    await skill.create_folder(ctx, "F")
    await skill.add_chart(ctx, "F", _chart(songdb, "STD:MAS"))
    await skill.add_chart(ctx, "F", _chart(songdb, "DX:MAS"))

    removed = await skill.remove_chart(ctx, "F", _chart(songdb, "STD:MAS"))

    assert removed.data["removed"] is True
    left = (await skill.show_folder(ctx, "F")).table
    assert left is not None and [tc.row_chart_key(r) for r in left.rows] == [("Both", "DX", "MAS")]


async def test_bulk_save_and_add_keep_the_chart_type_from_the_table(skill, db, songdb) -> None:
    ctx = _ctx(db, songdb)
    builder = TableBuilder(unit="채보")
    columns = tc.identity_columns("maimai")
    tc.add_columns(builder, *columns, game="maimai")
    song = songdb.song_by_title("Both")
    assert song is not None
    for key in ("STD:MAS", "DX:MAS"):
        chart = song.charts[key]
        builder.row(tc.chart_ref("Both", "MAS", chart.chart_type), tc.chart_cells(song, chart, columns))
    table = builder.build()

    saved = await skill.save_table(ctx, table, "SAVED")
    await skill.create_folder(ctx, "EMPTY")
    added = await skill.add_to_folder(ctx, "EMPTY", table)

    assert saved.data["count"] == 2 and added.data["count"] == 2
    assert added.data["charts"] == ["Both STD MAS", "Both DX MAS"]


async def test_folders_are_per_game(skill, db, songdb) -> None:
    await skill.create_folder(_ctx(db, songdb), "공용 이름")

    other = await skill.list_folders(_ctx(db, songdb, game="chunithm"))
    mine = await skill.list_folders(_ctx(db, songdb))

    assert other.data == {"folders": []}
    assert [f["name"] for f in mine.data["folders"]] == ["공용 이름"]
