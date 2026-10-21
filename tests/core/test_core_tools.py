from pathlib import Path

import aiosqlite

from core.core_tools import CORE_TOOL_SPECS, list_capabilities, open_skill, remember
from core.skill_loading import SkillLoadState
from core.tool_types import ToolContext
from services.common.memory import MemoryService


def test_core_tool_specs_contains_all_core_tools() -> None:
    names = {spec.name for spec in CORE_TOOL_SPECS}
    assert names == {"open_skill", "remember", "list_capabilities", "table_query", "table_peek"}


def test_list_capabilities_importable_without_core_capabilities_module() -> None:
    # list_capabilities는 core/capabilities.py를 함수 안에서 늦게 import한다. 이 모듈을
    # import하는 것만으로는(= 이 함수를 직접 부르지 않는 한) 깨지지 않아야 한다.
    assert list_capabilities.__name__ == "list_capabilities"


async def test_open_skill_marks_skill_open_in_state() -> None:
    state = SkillLoadState(mode="instructions", known_skills={"chunithm-border"})
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={"_skill_load_state": state})
    result = await open_skill(ctx, skill_name="chunithm-border")
    assert result.error is None
    assert state.level_for("chunithm-border") == "full"


async def test_open_skill_rejects_unknown_skill() -> None:
    state = SkillLoadState(mode="instructions", known_skills={"chunithm-border"})
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={"_skill_load_state": state})
    result = await open_skill(ctx, skill_name="does-not-exist")
    assert result.error is not None


async def _memory_ctx() -> tuple[ToolContext, MemoryService, aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    for name in ("004_memory.sql", "015_memory_nickname_pending.sql"):
        await conn.executescript(Path("migrations/core", name).read_text())
    memory = MemoryService(conn)
    return ToolContext(player_id="p1", tags=frozenset(), services={"memory": memory}), memory, conn


async def test_remember_appends_to_memory() -> None:
    ctx, memory, conn = await _memory_ctx()
    try:
        result = await remember(ctx, content="왼손이 약하다")
        assert result.error is None
        assert result.data == {"saved": "왼손이 약하다", "nickname": None}
        assert await memory.get("p1") == "왼손이 약하다"
    finally:
        await conn.close()


async def test_remember_nickname_goes_to_the_nickname_slot() -> None:
    ctx, memory, conn = await _memory_ctx()
    try:
        result = await remember(ctx, nickname="디짐")
        assert result.error is None
        assert result.data == {"saved": None, "nickname": "디짐"}
        assert await memory.get_nickname("p1") == "디짐"
        assert await memory.get("p1") == ""
    finally:
        await conn.close()


async def test_remember_rejects_empty_input() -> None:
    ctx, _memory, conn = await _memory_ctx()
    try:
        assert (await remember(ctx)).error is not None
        assert (await remember(ctx, content="  ", nickname="@")).error is not None
    finally:
        await conn.close()


async def test_end_conversation_marks_the_request_inside_a_thread() -> None:
    from core.core_tools import THREAD_TOOL_SPECS, end_conversation
    from core.threads import ThreadEndRequest

    request = ThreadEndRequest()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={"_thread_end": request})

    result = await end_conversation(ctx)

    assert result.error is None
    assert request.requested is True
    assert {spec.name for spec in THREAD_TOOL_SPECS} == {"end_conversation"}


async def test_end_conversation_refuses_outside_a_thread() -> None:
    from core.core_tools import end_conversation

    result = await end_conversation(ToolContext(player_id="p1", tags=frozenset()))

    assert result.error is not None


# ---- table_query / table_peek ----

from typing import Any

from core.declarations import build_tool_declaration
from core.executor import ToolExecutor
from core.permissions import InMemoryTagProvider
from core.resolvers import ResolutionError, ResolverRegistry
from core.tables import RowRef, TableBuilder, TableStore, cell
from core.tool_types import ScoreRef

from core.core_tools import table_peek, table_query  # isort: skip


class _ScoreResolver:
    async def resolve(self, raw: str, ctx: ToolContext) -> int:
        scores = {"SSS": 1_007_500, "SS": 1_000_000}
        if raw in scores:
            return scores[raw]
        if raw.isdigit():
            return int(raw)
        raise ResolutionError(message="찾지 못함")


def _env() -> tuple[ToolExecutor, ToolContext]:
    store = TableStore()
    builder = TableBuilder().column("song", "곡").column("score", "점수", "ScoreRef").column("rank", "랭크", "RankRef")
    for key, (song, score) in enumerate([("A", 1_005_000), ("B", 1_009_000), ("C", None)], start=1):
        builder.row(RowRef("k", str(key)), {"song": cell(song), "score": cell(score), "rank": cell(None)})
    _ = store.put("p1", "c1", builder.build(), now=0)
    resolvers = ResolverRegistry()
    resolvers.register(ScoreRef, _ScoreResolver())
    executor = ToolExecutor(
        resolvers=resolvers, tag_provider=InMemoryTagProvider(), tag_definitions={}, tables=store
    )
    ctx = ToolContext(
        player_id="p1", tags=frozenset(), channel_id="c1",
        services={"_ref_resolvers": resolvers, "_turn_table_ids": []},
    )
    return executor, ctx


def _tool(fn: Any) -> Any:
    return fn.__yuni_tool__


async def _query(spec: Any, args: dict[str, Any]) -> Any:
    executor, ctx = _env()
    outcome = await executor.invoke(_tool(spec), ctx, args)
    assert outcome.result is not None
    return outcome.result


async def test_table_query_value_is_parsed_by_the_columns_value_type() -> None:
    result = await _query(table_query, {"table": "t1", "where": [{"column": "점수", "op": "<", "value": "SSS"}]})

    assert result.error is None and result.table is not None
    assert [row.cell("song").raw for row in result.table.rows] == ["A", "C"]  # B(1,009,000)는 SSS 이상, C는 값 없음이라 통과
    assert result.table.steps == ("점수<SSS",)


async def test_table_query_unresolvable_value_and_unknown_column_are_errors() -> None:
    bad_value = await _query(table_query, {"table": "t1", "where": [{"column": "점수", "op": "<", "value": "???"}]})
    assert bad_value.error == "'점수' 칸 값 '???'을(를) 해석하지 못했다"
    no_column = await _query(table_query, {"table": "t1", "where": [{"column": "없음", "op": "<", "value": "1"}]})
    assert no_column.error == "그 표에 '없음' 칸이 없다"
    unresolvable_type = await _query(table_query, {"table": "t1", "where": [{"column": "랭크", "op": "==", "value": "SS"}]})
    assert unresolvable_type.error == "'랭크' 칸 값을 해석할 수 없다"


async def test_table_query_number_value_must_be_numeric() -> None:
    executor, ctx = _env()
    builder = TableBuilder().column("n", "수", "number").row(RowRef("k", "1"), {"n": cell(5)})
    assert executor.tables is not None
    _ = executor.tables.put("p1", "c1", builder.build(), now=0)
    outcome = await executor.invoke(
        _tool(table_query), ctx, {"table": "t2", "where": [{"column": "수", "op": ">", "value": "abc"}]}
    )
    assert outcome.result is not None and outcome.result.error == "'수' 칸 값 'abc'은 숫자가 아니다"
    ok = await executor.invoke(
        _tool(table_query), ctx, {"table": "t2", "where": [{"column": "수", "op": ">", "value": "1,0"}]}
    )
    assert ok.result is not None and ok.result.error is None  # 쉼표는 천 단위 구분으로 본다(10보다 작지 않으면 통과)


async def test_table_query_rows_refused_on_this_turns_table() -> None:
    executor, ctx = _env()
    ctx.services["_turn_table_ids"].append("t1")  # pyright: ignore[reportIndexIssue,reportAttributeAccessIssue]
    outcome = await executor.invoke(_tool(table_query), ctx, {"table": "t1", "rows": [1]})
    assert outcome.result is not None and outcome.result.error == "번호는 사람이 본 표에만 쓴다"
    peek = await executor.invoke(_tool(table_peek), ctx, {"table": "t1", "rows": [1]})
    assert peek.result is not None and peek.result.error == "번호는 사람이 본 표에만 쓴다"
    plain = await executor.invoke(_tool(table_query), ctx, {"table": "t1"})
    assert plain.result is not None and plain.result.error is None  # 번호 없이는 된다


async def test_table_peek_returns_data_without_a_table() -> None:
    result = await _query(table_peek, {"table": "t1", "rows": [2]})
    assert result.table is None and result.display is None
    assert result.data["rows"] == [{"number": 2, "values": {"곡": "B", "점수": 1_009_000, "랭크": None}}]


def test_table_tools_declare_array_parameters_for_the_model() -> None:
    declaration = build_tool_declaration(_tool(table_query), skill_name=None)
    properties = declaration.parameters_json_schema["properties"]
    assert properties["table"] == {"type": "string"}
    assert properties["rows"] == {"type": "array", "items": {"type": "integer"}}
    assert properties["exclude_rows"] == {"type": "array", "items": {"type": "integer"}}
    assert properties["where"] == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"column": {"type": "string"}, "op": {"type": "string"}, "value": {"type": "string"}},
            "required": ["column", "op", "value"],
        },
    }
    assert properties["descending"] == {"type": "boolean"}
    assert declaration.parameters_json_schema["required"] == ["table"]


async def test_table_query_ordered_column_value_is_matched_to_the_listed_name() -> None:
    executor, ctx = _env()
    builder = TableBuilder().column("song", "곡").column("version", "버전", order=("OLD", "MID", "NEW"))
    for key, (song, version) in enumerate([("a", "OLD"), ("b", "NEW"), ("c", "MID")], start=1):
        builder.row(RowRef("k", str(key)), {"song": cell(song), "version": cell(version)})
    assert executor.tables is not None
    _ = executor.tables.put("p1", "c1", builder.build(), now=0)

    ok = await executor.invoke(
        _tool(table_query), ctx, {"table": "t2", "where": [{"column": "버전", "op": ">=", "value": "mid"}]}
    )
    assert ok.result is not None and ok.result.table is not None
    assert [row.cell("song").raw for row in ok.result.table.rows] == ["b", "c"]
    bad = await executor.invoke(
        _tool(table_query), ctx, {"table": "t2", "where": [{"column": "버전", "op": ">=", "value": "VERSE"}]}
    )
    assert bad.result is not None and bad.result.error is not None and "쓸 수 있는 것: OLD, MID, NEW" in bad.result.error


async def test_table_query_columns_shows_only_the_named_columns() -> None:
    result = await _query(table_query, {"table": "t1", "columns": ["곡"]})
    assert result.error is None and result.table is not None
    assert [column.label for column in result.table.columns if not column.hidden_in_text] == ["곡"]
    assert result.table.steps == ("곡만 보임",)
    missing = await _query(table_query, {"table": "t1", "columns": ["없음"]})
    assert missing.error == "그 표에 '없음' 칸이 없다"


async def test_table_query_distinct_by_is_wired() -> None:
    result = await _query(table_query, {"table": "t1", "distinct_by": "랭크"})
    assert result.error is None and result.table is not None
    assert result.table.steps == ("랭크마다 첫 행만",)
    assert [row.cell("song").raw for row in result.table.rows] == ["A", "B", "C"]  # 랭크가 전부 비어 겹치지 않는다
