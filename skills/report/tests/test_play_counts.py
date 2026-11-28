# skills/report/tests/test_play_counts.py
"""플레이 횟수 도구(2026-11-27 사용자 요청, 2026-11-28 저장값 방식). `play_counts` 서비스는 가짜다."""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest
from chuni_penguin.adapters.errors import AuthenticationError

from core.events import EventBus
from core.tables import TableBuilder, WorkTable
from core.tool_types import ToolContext
from services.game import table_columns as tc
from services.game.chart_ref import ResolvedChart
from services.game.play_counts import PlayCountState
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
class _FakePlayCounts:
    """저장본: 키는 (제목, 채보 종류) 그대로 쓴다(진짜 서비스의 musicId 변환은 서비스 시험에서 본다)."""

    stored: dict[tuple[str, str], dict[str, int] | None] = field(default_factory=dict)
    saved: PlayCountState | None = None
    running: bool = False
    collect_error: Exception | None = None
    collected: list[str] = field(default_factory=list)

    def key_for(self, title: str, chart_type: str, difficulty: str = "") -> tuple[str, str] | None:
        return None if title == "곡 DB에 없음" else (title, chart_type)

    async def counts(self, player_id: str, keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, int] | None]:
        return {key: self.stored[key] for key in keys if key in self.stored}

    async def state(self, player_id: str) -> PlayCountState | None:
        return self.saved

    def collecting(self, player_id: str) -> bool:
        return self.running

    async def collect(self, player_id: str, *, ctx: ToolContext | None = None) -> None:
        self.collected.append(player_id)
        if self.collect_error is not None:
            raise self.collect_error


def _state(*, complete: bool = True) -> PlayCountState:
    return PlayCountState(total_plays=10, newest_play_at=None, complete=complete, updated_at="2026-11-28T03:00:00+00:00")


def _ctx(play_counts: _FakePlayCounts, *, game: str = "chunithm") -> ToolContext:
    return ToolContext(player_id="p1", tags=frozenset(), game=game, event_bus=EventBus(), services={"play_counts": play_counts})


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


def _chart(title: str, difficulty: str = "MAS") -> ResolvedChart:
    return ResolvedChart(song=SongRecord(title=title, aliases=(), artist="", id=1),
                         chart=ChartRecord(difficulty=difficulty, const=14.5, note_count=1000))


async def test_add_play_counts_fills_the_column_from_stored_counts_without_a_song_limit(skill: ModuleType) -> None:
    store = _FakePlayCounts(stored={("A", ""): {"MAS": 12, "EXP": 3}, ("B", ""): {"EXP": 1}}, saved=_state())
    rows = [("A", "", "MAS"), ("A", "", "EXP"), ("B", "", "MAS"), ("C", "", "MAS"), ("곡 DB에 없음", "", "MAS")]
    rows += [(f"곡{i}", "", "MAS") for i in range(60)]  # 예전 50곡 상한은 없다

    result = await skill.add_play_counts(_ctx(store), table=_chart_table(rows))

    assert result.error is None and result.table is not None
    texts = [r.cell("플레이 횟수").text for r in result.table.rows]
    # 저장본에 곡이 있는데 난이도가 없으면 0회, 마지막 수집이 다 받았는데 곡이 없으면 안 친 곡이라 0회, 곡 DB에 없으면 모름.
    assert texts[:5] == ["12회", "3회", "0회", "0회", "모름"]
    assert result.data == {"count": 65, "unknown": 1, "as_of": "2026-11-28T03:00:00+00:00", "collecting": False}


async def test_add_play_counts_marks_missing_songs_unknown_while_the_store_is_incomplete(skill: ModuleType) -> None:
    store = _FakePlayCounts(stored={("A", ""): {"MAS": 2}}, saved=_state(complete=False))

    result = await skill.add_play_counts(_ctx(store), table=_chart_table([("A", "", "MAS"), ("B", "", "MAS")]))

    assert result.table is not None
    assert [r.cell("플레이 횟수").text for r in result.table.rows] == ["2회", "모름"]


async def test_add_play_counts_column_is_numeric_for_filters(skill: ModuleType) -> None:
    store = _FakePlayCounts(stored={("A", ""): {"MAS": 12}}, saved=_state())

    result = await skill.add_play_counts(_ctx(store), table=_chart_table([("A", "", "MAS")]))

    assert result.table is not None
    assert result.table.column_by_label("플레이 횟수").value_type == "number"  # type: ignore[union-attr]
    assert result.table.rows[0].cell("플레이 횟수").raw == 12


async def test_add_play_counts_before_any_collection_says_so(skill: ModuleType) -> None:
    result = await skill.add_play_counts(_ctx(_FakePlayCounts()), table=_chart_table([("A", "", "MAS")]))
    assert result.error == "아직 플레이 횟수를 모아 두지 않았어. 동기화하면 뒤에서 모아 둘게."

    result = await skill.add_play_counts(_ctx(_FakePlayCounts(running=True)), table=_chart_table([("A", "", "MAS")]))
    assert result.error == "지금 플레이 횟수를 모으는 중이야. 조금 뒤에 다시 물어봐 줘."


async def test_add_play_counts_in_maimai_goes_by_title_and_chart_type(skill: ModuleType) -> None:
    store = _FakePlayCounts(stored={("Both", "DX"): {"MAS": 4}, ("Both", "STD"): {"MAS": 2}}, saved=_state())

    result = await skill.add_play_counts(
        _ctx(store, game="maimai"), table=_chart_table([("Both", "STD", "MAS"), ("Both", "DX", "MAS")])
    )

    assert result.table is not None
    assert [r.cell("플레이 횟수").text for r in result.table.rows] == ["2회", "4회"]


async def test_play_count_reads_one_chart(skill: ModuleType) -> None:
    store = _FakePlayCounts(stored={("A", ""): {"MAS": 12}}, saved=_state())

    result = await skill.play_count(_ctx(store), chart=_chart("A"))

    assert result.data["play_count"] == 12 and result.data["as_of"] == "2026-11-28T03:00:00+00:00"
    assert result.display is not None and result.display.content == "A MAS  12회"


async def test_play_count_of_a_missing_song_depends_on_whether_the_store_is_complete(skill: ModuleType) -> None:
    complete = await skill.play_count(_ctx(_FakePlayCounts(saved=_state())), chart=_chart("B"))
    assert complete.data["play_count"] == 0

    incomplete = await skill.play_count(_ctx(_FakePlayCounts(saved=_state(complete=False))), chart=_chart("B"))
    assert incomplete.error == "B MAS의 플레이 횟수는 아직 못 받았어. 다음 동기화 때 다시 받아 둘게."


async def test_songs_the_net_blocks_are_unknown_not_zero(skill: ModuleType) -> None:
    store = _FakePlayCounts(stored={("A", ""): {"MAS": 2}, ("X", ""): None}, saved=_state())

    table = await skill.add_play_counts(_ctx(store), table=_chart_table([("A", "", "MAS"), ("X", "", "MAS")]))
    one = await skill.play_count(_ctx(store), chart=_chart("X"))

    assert table.table is not None
    assert [r.cell("플레이 횟수").text for r in table.table.rows] == ["2회", "모름"]
    assert one.error == "X MAS — CHUNITHM-NET이 곡 상세를 막아 둔 곡이라 플레이 횟수를 몰라."


async def test_sync_saved_collects_in_the_background_and_swallows_login_errors(skill: ModuleType) -> None:
    store = _FakePlayCounts(collect_error=AuthenticationError())

    await skill.on_sync_saved(_ctx(store), {"player_id": "p1"})
    await skill.on_maimai_sync_saved(_ctx(store, game="maimai"), {"player_id": "p1"})

    assert store.collected == ["p1", "p1"]
