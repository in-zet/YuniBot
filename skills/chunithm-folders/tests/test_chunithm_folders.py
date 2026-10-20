# skills/chunithm-folders/tests/test_chunithm_folders.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
from core.tables import TableBuilder, WorkTable, render_text
from core.tool_types import ToolContext
from services.chunithm import table_columns as tc
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.songdb import ChartRecord, SongRecord
from services.chunithm.terms import ResolvedDifficulty

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_FOLDERS_SQL = Path(__file__).parent.parent / "migrations" / "001_init.sql"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_folders_skill_under_test", _SKILL_PY)
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
    try:
        yield conn
    finally:
        await conn.close()


class _Hit:
    def __init__(self, title: str, difficulty: str = "MAS", const: float | None = None) -> None:
        self.song = type("S", (), {"title": title})()
        self.chart = type("C", (), {"difficulty": difficulty, "const": const})()


@dataclass(slots=True, kw_only=True)
class _FakeSongDb:
    consts: dict[tuple[str, str], float] = field(default_factory=dict)
    const_range_hits: list[tuple[str, str, float | None]] = field(default_factory=list)
    level_hits: list[tuple[str, str]] = field(default_factory=list)
    version_hits: list[tuple[str, str]] = field(default_factory=list)

    def const_for(self, title: str, difficulty: str) -> float | None:
        return self.consts.get((title, difficulty))

    def chart_for(self, title: str, difficulty: str) -> ChartRecord | None:
        if (title, difficulty) not in self.consts:
            return None
        return ChartRecord(difficulty=difficulty, const=self.consts[(title, difficulty)], note_count=None)

    def find_by_const(self, *, min_const: float, max_const: float, difficulty: str | None = None) -> list[Any]:
        return [_Hit(title, dif, const) for title, dif, const in self.const_range_hits]

    def find_by_level(self, *, level: str, difficulty: str | None = None) -> list[Any]:
        return [_Hit(title, dif) for title, dif in self.level_hits]

    def find_by_version(self, *, version: str, difficulty: str | None = None) -> list[Any]:
        return [_Hit(title, dif) for title, dif in self.version_hits]


def _ctx(*, player_id: str = "p1", db: aiosqlite.Connection, songdb: _FakeSongDb | None = None) -> ToolContext:
    services: dict[str, object] = {"songdb": songdb or _FakeSongDb()}
    return ToolContext(player_id=player_id, tags=frozenset(), services=services, db=db, event_bus=EventBus())


def _chart(title: str, difficulty: str = "MAS") -> ResolvedChart:
    return ResolvedChart(song=SongRecord(title=title, aliases=(), artist="", id=1), chart=ChartRecord(difficulty=difficulty, const=None, note_count=None))


_MAS = ResolvedDifficulty(canonical="MASTER")


# ---------------------------------------------------------------- 폴더 생성/삭제/이름변경


async def test_create_folder_then_duplicate_rejected(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    result = await skill.create_folder(ctx, "상수 15.0 폴더")
    assert result.error is None
    dup = await skill.create_folder(ctx, "상수 15.0 폴더")
    assert dup.error is not None


async def test_create_folder_duplicate_case_insensitive(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "레벨 14+")
    dup = await skill.create_folder(ctx, "레벨 14+".upper())
    assert dup.error is not None


async def test_delete_folder_removes_charts_and_note(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "F1")
    await skill.add_chart(ctx, "F1", _chart("크로노 마기아"))
    await skill.set_note(ctx, "F1", "메모")

    result = await skill.delete_folder(ctx, "F1")
    assert result.error is None

    missing = await skill.show_folder(ctx, "F1")
    assert missing.error is not None


async def test_delete_missing_folder_lists_existing_names(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "F1")
    result = await skill.delete_folder(ctx, "없는 폴더")
    assert result.error is not None
    assert "F1" in result.error


async def test_rename_folder_success(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "구이름")
    result = await skill.rename_folder(ctx, "구이름", "새이름")
    assert result.error is None
    shown = await skill.show_folder(ctx, "새이름")
    assert shown.error is None


async def test_rename_folder_name_clash_rejected(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "A")
    await skill.create_folder(ctx, "B")
    result = await skill.rename_folder(ctx, "A", "B")
    assert result.error is not None


async def test_rename_folder_to_same_name_is_allowed(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "A")
    result = await skill.rename_folder(ctx, "A", "A")
    assert result.error is None


# ---------------------------------------------------------------- 목록/보기


async def test_list_folders_shows_count_and_note_flag(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "F1")
    await skill.add_chart(ctx, "F1", _chart("크로노 마기아"))
    await skill.set_note(ctx, "F1", "메모")
    await skill.create_folder(ctx, "F2")

    result = await skill.list_folders(ctx)
    by_name = {f["name"]: f for f in result.data["folders"]}
    assert by_name["F1"]["chart_count"] == 1
    assert by_name["F1"]["has_note"] is True
    assert by_name["F2"]["chart_count"] == 0
    assert by_name["F2"]["has_note"] is False


async def test_show_folder_includes_note_and_charts_with_const(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(consts={("크로노 마기아", "MAS"): 15.0})
    ctx = _ctx(db=db, songdb=songdb)
    await skill.create_folder(ctx, "F1")
    await skill.add_chart(ctx, "F1", _chart("크로노 마기아"))
    await skill.set_note(ctx, "F1", "메모")

    result = await skill.show_folder(ctx, "F1")
    assert result.data["note"] == "메모"
    table = result.table
    assert table is not None and result.display is None
    assert table.subject == "p1" and table.unit == "채보"
    assert table.summary == {"폴더": "F1", "메모": "메모"}
    assert [c.name for c in table.columns] == [tc.SONG, tc.DIFFICULTY, tc.CONST]
    (row,) = table.rows
    assert tc.chart_key(row.ref) == ("크로노 마기아", "MAS")
    assert row.cell(tc.CONST).raw == 15.0 and row.cell(tc.CONST).text == "15.0"
    assert render_text(table).content.endswith("1. 크로노 마기아  MAS  상수 15.0")


async def test_show_folder_empty_folder_says_so_without_table(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "F1")
    await skill.set_note(ctx, "F1", "비었음")

    result = await skill.show_folder(ctx, "F1")

    assert result.table is None
    assert result.display is not None and "채보가 없어" in (result.display.content or "")
    assert result.data["count"] == 0


async def test_show_folder_missing_folder_error(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    result = await skill.show_folder(ctx, "없음")
    assert result.error is not None


# ---------------------------------------------------------------- 채보 한 개씩


async def test_add_chart_duplicate_is_noop(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "F1")
    first = await skill.add_chart(ctx, "F1", _chart("곡"))
    second = await skill.add_chart(ctx, "F1", _chart("곡"))
    assert first.data["added"] is True
    assert second.data["added"] is False
    shown = await skill.show_folder(ctx, "F1")
    assert shown.table is not None and shown.table.row_count == 1


async def test_remove_chart_missing_is_noop(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "F1")
    result = await skill.remove_chart(ctx, "F1", _chart("없는 곡"))
    assert result.data["removed"] is False


async def test_remove_chart_present_removes_it(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "F1")
    await skill.add_chart(ctx, "F1", _chart("곡"))
    result = await skill.remove_chart(ctx, "F1", _chart("곡"))
    assert result.data["removed"] is True
    shown = await skill.show_folder(ctx, "F1")
    assert shown.table is None and shown.data["count"] == 0


# ---------------------------------------------------------------- 조건 일괄


async def test_add_by_condition_requires_exactly_one_filter(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "F1")

    none_given = await skill.add_by_condition(ctx, "F1")
    assert none_given.error is not None

    both_given = await skill.add_by_condition(ctx, "F1", min_const=15.0, max_const=15.0, level="14+")
    assert both_given.error is not None


async def test_add_by_condition_const_range_requires_both_bounds(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "F1")
    result = await skill.add_by_condition(ctx, "F1", min_const=15.0)
    assert result.error is not None


async def test_add_by_condition_dry_run_does_not_change_folder(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(const_range_hits=[("곡A", "MAS", 15.0), ("곡B", "ULT", 15.0)])
    ctx = _ctx(db=db, songdb=songdb)
    await skill.create_folder(ctx, "F1")

    result = await skill.add_by_condition(ctx, "F1", min_const=15.0, max_const=15.0, dry_run=True)
    assert result.data["count"] == 2
    shown = await skill.show_folder(ctx, "F1")
    assert shown.table is None and shown.data["count"] == 0


async def test_add_by_condition_excludes_existing_and_is_idempotent(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(const_range_hits=[("곡A", "MAS", 15.0), ("곡B", "ULT", 15.0)])
    ctx = _ctx(db=db, songdb=songdb)
    await skill.create_folder(ctx, "F1")
    await skill.add_chart(ctx, "F1", _chart("곡A", "MAS"))

    result = await skill.add_by_condition(ctx, "F1", min_const=15.0, max_const=15.0)
    assert result.data["count"] == 1
    assert result.data["charts"] == ["곡B ULT"]

    again = await skill.add_by_condition(ctx, "F1", min_const=15.0, max_const=15.0)
    assert again.data["count"] == 0


async def test_remove_by_condition_only_removes_present_ones(skill: ModuleType, db: aiosqlite.Connection) -> None:
    songdb = _FakeSongDb(level_hits=[("곡A", "MAS"), ("곡B", "ULT")])
    ctx = _ctx(db=db, songdb=songdb)
    await skill.create_folder(ctx, "F1")
    await skill.add_chart(ctx, "F1", _chart("곡A", "MAS"))

    result = await skill.remove_by_condition(ctx, "F1", level="14+")
    assert result.data["count"] == 1
    assert result.data["charts"] == ["곡A MAS"]
    shown = await skill.show_folder(ctx, "F1")
    assert shown.table is None and shown.data["count"] == 0


# ---------------------------------------------------------------- 메모


async def test_set_note_overwrite_append_and_clear(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "F1")

    await skill.set_note(ctx, "F1", "첫 메모")
    overwritten = await skill.set_note(ctx, "F1", "덮어쓴 메모")
    assert overwritten.data["note"] == "덮어쓴 메모"

    appended = await skill.set_note(ctx, "F1", "덧붙임", append=True)
    assert appended.data["note"] == "덮어쓴 메모\n덧붙임"

    cleared = await skill.set_note(ctx, "F1", "")
    assert cleared.data["note"] is None


# ---------------------------------------------------------------- save_table


def _chart_table(*charts: tuple[str | None, str | None]) -> WorkTable:
    builder = TableBuilder(unit="채보")
    tc.add_columns(builder, tc.SONG, tc.DIFFICULTY)
    builder.step("검색 결과")
    for title, difficulty in charts:
        ref = tc.song_ref(title) if difficulty is None and title else tc.chart_ref(title or "", difficulty or "")
        builder.row(ref, {tc.SONG: tc.song_cell(title), tc.DIFFICULTY: tc.difficulty_cell(difficulty)})
    return builder.build()


async def test_save_table_creates_new_folder_with_the_chart_rows(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)

    result = await skill.save_table(ctx, _chart_table(("A", "MAS"), ("B", "EXP"), ("A", "MAS")), "새 폴더")

    assert result.error is None
    assert result.data["count"] == 2  # 같은 채보가 두 번 있어도 한 번만 담는다
    shown = await skill.show_folder(ctx, "새 폴더")
    assert shown.table is not None
    assert [tc.chart_key(r.ref) for r in shown.table.rows] == [("A", "MAS"), ("B", "EXP")]


async def test_save_table_rejects_existing_folder_name_case_insensitively(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    ctx = _ctx(db=db)
    await skill.create_folder(ctx, "F1")

    result = await skill.save_table(ctx, _chart_table(("A", "MAS")), "f1")
    dry = await skill.save_table(ctx, _chart_table(("A", "MAS")), "f1", dry_run=True)

    assert result.error is not None and "이미 있어" in result.error
    assert dry.error is not None
    assert (await skill.show_folder(ctx, "F1")).data["count"] == 0


async def test_save_table_dry_run_writes_nothing_and_reports_count(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)

    preview = await skill.save_table(ctx, _chart_table(("A", "MAS"), ("B", "MAS")), "새", dry_run=True)

    assert preview.data["count"] == 2
    assert (await skill.list_folders(ctx)).data["folders"] == []


async def test_save_table_through_executor_drops_rows_without_chart_and_asks_confirm(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    from core.executor import ToolExecutor
    from core.permissions import InMemoryTagProvider
    from core.resolvers import ResolverRegistry
    from core.tables import TableStore

    store = TableStore()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={}, tables=store,
        partial_row_hints=tc.PARTIAL_ROW_HINTS,
    )
    ctx = ToolContext(
        player_id="p1", tags=frozenset(), services={"songdb": _FakeSongDb()}, db=db,
        event_bus=EventBus(), channel_id="c1",
    )
    table_id = store.put("p1", "c1", _chart_table(("A", "MAS"), ("B", "EXP"), ("S", None), (None, None)))

    outcome = await executor.invoke(skill.save_table.__yuni_tool__, ctx, {"table": table_id, "name": "폴더"})

    assert outcome.needs_confirm and outcome.preview is not None
    assert outcome.preview.data["count"] == 2
    assert any("곡 단위 행(난이도 없음) 1개 뺌" in note for note in outcome.preview.data["notes"])
    assert outcome.confirm_token is not None
    done = await executor.confirm(outcome.confirm_token, confirmed=True, player_id="p1")
    assert done.error is None and done.data["count"] == 2
    assert (await skill.show_folder(ctx, "폴더")).data["count"] == 2


async def test_save_table_is_refused_for_a_stale_table(skill: ModuleType, db: aiosqlite.Connection) -> None:
    from core.executor import ToolExecutor
    from core.permissions import InMemoryTagProvider
    from core.resolvers import ResolverRegistry
    from core.tables import TableStore

    store = TableStore()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={}, tables=store,
    )
    ctx = ToolContext(
        player_id="p1", tags=frozenset(), services={"songdb": _FakeSongDb()}, db=db,
        event_bus=EventBus(), channel_id="c1",
    )
    table_id = store.put("p1", "c1", _chart_table(("A", "MAS")), stale=True)

    outcome = await executor.invoke(skill.save_table.__yuni_tool__, ctx, {"table": table_id, "name": "폴더"})

    assert outcome.result is not None and outcome.result.error is not None
    assert "동기화 전 값" in outcome.result.error
