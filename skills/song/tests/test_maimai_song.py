# skills/song/tests/test_maimai_song.py
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from core.events import EventBus
from core.tables import WorkTable
from core.tool_types import ToolContext
from services.game import table_columns as tc
from services.game.chart_ref import ResolvedChart
from services.game.terms import TermsService
from services.maimai.profile import MAIMAI
from services.maimai.songdb import MaimaiSongDbService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"

# 실제 항목 모양을 본뜬 만든 표본(저장소에 원본을 넣지 않는다).
_SAMPLE = [
    {
        "title": "Both", "artist": "작곡가A", "version": "20000", "image_url": "b.png", "intl": "1",
        "lev_mas": "12+", "lev_mas_i": "12.6", "lev_mas_notes": "346",
        "dx_lev_mas": "13+", "dx_lev_mas_i": "13.7", "dx_lev_mas_notes": "631",
        "dx_lev_remas": "14", "dx_lev_remas_i": "14.2", "dx_lev_remas_notes": "700",
    },
    {
        "title": "Fresh", "artist": "작곡가B", "version": "26511", "image_url": "f.png", "intl": "1",
        "dx_lev_mas": "14+", "dx_lev_mas_i": "14.9", "dx_lev_mas_notes": "900",
    },
    {
        "title": "[宴]Party", "artist": "작곡가A", "version": "24010", "image_url": "p.png", "intl": "1",
        "lev_utage": "13+?", "lev_utage_notes": "950",
    },
]


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_maimai_song_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def songdb(tmp_path: Path) -> MaimaiSongDbService:
    path = tmp_path / "music-ex-intl.json"
    path.write_text(json.dumps(_SAMPLE, ensure_ascii=False), encoding="utf-8")
    return MaimaiSongDbService(path)


def _ctx(songdb: MaimaiSongDbService) -> ToolContext:
    terms = TermsService(MAIMAI.terms_path, game="maimai", percent_scores=True)
    return ToolContext(
        player_id="p1", tags=frozenset(), services={"songdb": songdb, "terms": terms},
        event_bus=EventBus(), game="maimai",
    )


async def _difficulty(ctx: ToolContext, raw: str):
    return await ctx.terms.resolve_difficulty(raw, ctx)


def _rows(table: WorkTable | None) -> list[tuple[object, ...]]:
    assert table is not None
    return [tc.row_chart_key(row) for row in table.rows]


async def test_info_lists_every_chart_type_in_display_order(songdb) -> None:
    skill = _load_skill()
    song = songdb.song_by_title("Both")

    result = await skill.info(_ctx(songdb), song=song)

    lines = (result.display.content or "").splitlines() if result.display else []
    charts = [line.split(" Lv.")[0] for line in lines if " Lv." in line]
    assert charts == ["STD MAS", "DX MAS", "DX REM"]


async def test_chart_names_the_chart_type_and_emits_the_games_event(songdb) -> None:
    skill = _load_skill()
    song = songdb.song_by_title("Both")
    chart = song.charts["DX:REM"]
    seen: list[tuple[str, object]] = []

    class _Bus(EventBus):
        async def emit_gather(self, event_name, payload, *, player_id, guild_id):  # type: ignore[override]
            seen.append((event_name, payload))
            return []

    ctx = _ctx(songdb)
    ctx = ToolContext(player_id="p1", tags=frozenset(), services=ctx.services, event_bus=_Bus(), game="maimai")

    result = await skill.chart(ctx, chart=ResolvedChart(song=song, chart=chart))

    assert result.data["chart_type"] == "DX" and result.data["const"] == 14.2
    assert seen == [
        ("maimai.song.viewed", {"player_id": "p1", "song_title": "Both", "difficulty": "REM", "chart_type": "DX"})
    ]
    assert result.display is not None and "DX REM" in (result.display.content or "")


async def test_find_by_const_returns_typed_rows_with_a_type_column(songdb) -> None:
    skill = _load_skill()
    ctx = _ctx(songdb)

    result = await skill.find_by_const(ctx, min_const=12.0, difficulty=await _difficulty(ctx, "MAS"))

    table = result.table
    assert table is not None
    assert [c.name for c in table.columns][:3] == [tc.SONG, tc.CHART_TYPE, tc.DIFFICULTY]
    assert _rows(table) == [("Fresh", "DX", "MAS"), ("Both", "DX", "MAS"), ("Both", "STD", "MAS")]  # 상수 내림차순


async def test_find_songs_handles_remaster_and_hides_utage_from_ranges(songdb) -> None:
    skill = _load_skill()
    ctx = _ctx(songdb)

    result = await skill.find_songs(ctx, min_const=14.0, difficulty=await _difficulty(ctx, "REM"))

    assert _rows(result.table) == [("Both", "DX", "REM")]


async def test_find_by_version_with_unknown_version_lists_the_names(songdb) -> None:
    skill = _load_skill()

    result = await skill.find_by_version(_ctx(songdb), version="NO SUCH")

    assert result.error is not None and "CiRCLE PLUS" in result.error


async def test_find_by_version_finds_the_charts_of_that_version(songdb) -> None:
    skill = _load_skill()

    result = await skill.find_by_version(_ctx(songdb), version="CiRCLE PLUS")

    assert _rows(result.table) == [("Fresh", "DX", "MAS")]


async def test_find_by_artist_with_difficulty_returns_both_chart_types(songdb) -> None:
    skill = _load_skill()
    ctx = _ctx(songdb)

    result = await skill.find_by_artist(ctx, artist="작곡가A", difficulty=await _difficulty(ctx, "MAS"))

    assert _rows(result.table) == [("Both", "STD", "MAS"), ("Both", "DX", "MAS")]


async def test_charts_of_expands_a_song_into_each_chart_type_of_the_difficulty(songdb) -> None:
    skill = _load_skill()
    ctx = _ctx(songdb)
    songs = await skill.find_by_artist(ctx, artist="작곡가A")  # 곡 단위 표
    assert songs.table is not None

    result = await skill.charts_of(ctx, table=songs.table)

    assert _rows(result.table) == [("Both", "STD", "MAS"), ("Both", "DX", "MAS")]  # 宴 곡은 MAS가 없어 빠진다
    assert result.data["difficulty"] == "MAS"


async def test_charts_of_with_utage_difficulty_finds_the_utage_song(songdb) -> None:
    skill = _load_skill()
    ctx = _ctx(songdb)
    songs = await skill.find_by_artist(ctx, artist="작곡가A")
    assert songs.table is not None

    result = await skill.charts_of(ctx, table=songs.table, difficulty=await _difficulty(ctx, "UTA"))

    assert _rows(result.table) == [("[宴]Party", "STD", "UTA")]

