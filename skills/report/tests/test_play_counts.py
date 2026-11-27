# skills/report/tests/test_play_counts.py
"""플레이 횟수 도구(2026-11-27 사용자 요청). NET 서비스는 `play_counts`만 가진 가짜다."""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from chuni_penguin.adapters.errors import MaintenanceError

from core.events import EventBus
from core.tables import TableBuilder, WorkTable
from core.tool_types import ToolContext
from services.game import table_columns as tc
from services.game.chart_ref import ResolvedChart
from services.game.songdb import ChartRecord, SongRecord

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


@pytest.fixture
def skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_report_play_counts_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(slots=True, kw_only=True)
class _FakeNet:
    counts: dict[Any, dict[str, int]] = field(default_factory=dict)
    error: Exception | None = None
    asked: list[list[Any]] = field(default_factory=list)

    async def play_counts(self, player_id: str, keys: list[Any], *, ctx: ToolContext | None = None) -> dict[Any, dict[str, int]]:
        self.asked.append(list(keys))
        if self.error is not None:
            raise self.error
        return {key: self.counts[key] for key in keys if key in self.counts}


@dataclass(slots=True, kw_only=True)
class _FakeSongdb:
    songs: dict[str, SongRecord]

    def song_by_title(self, title: str) -> SongRecord | None:
        return self.songs.get(title)


def _song(title: str, song_id: int) -> SongRecord:
    return SongRecord(title=title, aliases=(), artist="", id=song_id)


def _ctx(net: _FakeNet, songs: list[SongRecord], *, game: str = "chunithm") -> ToolContext:
    return ToolContext(
        player_id="p1", tags=frozenset(), game=game, event_bus=EventBus(),
        services={"chunithm_net": net, "songdb": _FakeSongdb(songs={s.title: s for s in songs})},
    )


def _chart_table(rows: list[tuple[str, str, str]]) -> WorkTable:
    builder = TableBuilder(unit="채보")
    tc.add_columns(builder, tc.SONG, tc.CHART_TYPE, tc.DIFFICULTY)
    builder.step("테스트 채보 표")
    for title, chart_type, code in rows:
        builder.row(
            tc.chart_ref(title, code, chart_type),
            {tc.SONG: tc.song_cell(title), tc.CHART_TYPE: tc.text_cell(chart_type), tc.DIFFICULTY: tc.difficulty_cell(code)},
        )
    return builder.build()


async def test_add_play_counts_asks_each_song_once_and_fills_the_column(skill: ModuleType) -> None:
    net = _FakeNet(counts={1: {"MAS": 12, "EXP": 3}, 2: {"EXP": 1}})
    songs = [_song("A", 1), _song("B", 2), _song("C", 3)]

    result = await skill.add_play_counts(
        _ctx(net, songs), table=_chart_table([("A", "", "MAS"), ("A", "", "EXP"), ("B", "", "MAS"), ("C", "", "MAS")])
    )

    assert result.error is None and result.table is not None
    assert net.asked == [[1, 2, 3]]  # 곡마다 한 번
    assert [r.cell("플레이 횟수").text for r in result.table.rows] == ["12회", "3회", "0회", "모름"]
    assert result.data == {"count": 4, "songs": 3, "unknown": 1}


async def test_add_play_counts_refuses_more_than_50_songs_without_asking(skill: ModuleType) -> None:
    net = _FakeNet()
    songs = [_song(f"곡{i}", i) for i in range(51)]

    result = await skill.add_play_counts(_ctx(net, songs), table=_chart_table([(s.title, "", "MAS") for s in songs]))

    assert result.error == "플레이 횟수는 한 번에 곡 50개까지만 받을 수 있어(지금 51개). 표를 먼저 걸러 줘."
    assert net.asked == []


async def test_add_play_counts_in_maimai_goes_by_title_and_chart_type(skill: ModuleType) -> None:
    net = _FakeNet(counts={("Both", "DX"): {"MAS": 4}, ("Both", "STD"): {"MAS": 2}})

    result = await skill.add_play_counts(
        _ctx(net, [_song("Both", 9)], game="maimai"), table=_chart_table([("Both", "STD", "MAS"), ("Both", "DX", "MAS")])
    )

    assert result.table is not None
    assert net.asked == [[("Both", "STD"), ("Both", "DX")]]
    assert [r.cell("플레이 횟수").text for r in result.table.rows] == ["2회", "4회"]


async def test_play_count_reads_one_chart(skill: ModuleType) -> None:
    song = _song("A", 1)
    chart = ChartRecord(difficulty="MAS", const=14.5, note_count=1000)
    net = _FakeNet(counts={1: {"MAS": 12}})

    result = await skill.play_count(_ctx(net, [song]), chart=ResolvedChart(song=song, chart=chart))

    assert result.data["play_count"] == 12
    assert result.display is not None and result.display.content == "A MAS  12회"


async def test_play_count_reports_maintenance(skill: ModuleType) -> None:
    song = _song("A", 1)
    chart = ChartRecord(difficulty="MAS", const=14.5, note_count=1000)

    result = await skill.play_count(
        _ctx(_FakeNet(error=MaintenanceError()), [song]), chart=ResolvedChart(song=song, chart=chart)
    )

    assert result.error == "CHUNITHM-NET이 점검 중이야. 점검이 끝나면 다시 해 줘."
