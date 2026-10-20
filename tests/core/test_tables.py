from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from core.tables import (
    TABLE_TTL_SECONDS,
    Cell,
    PartialRowHint,
    RowRef,
    TableArg,
    TableBuilder,
    TableError,
    TableNeeds,
    TableStore,
    WorkTable,
    cell,
    parse_table_arg,
    resolve_table_arg,
    row_at,
)


def _table() -> WorkTable:
    builder = TableBuilder(subject="p1", unit="채보")
    builder.column("song", "곡", bare=True).column("const", "상수", "number")
    builder.group("15.5", {"평균": "15.5"})
    builder.row(
        RowRef("chunithm.chart", "a"), {"song": cell("A"), "const": cell(15.5, "15.5")}
    )
    builder.row(
        RowRef("chunithm.chart", "b"), {"song": cell("B"), "const": cell(15.5, "15.5")}
    )
    builder.group("15.0")
    builder.row(RowRef("chunithm.chart", "c"), {"song": cell("C")})
    builder.summary({"닉네임": "유니"}).step("15 이상").row_formats(["{곡} {상수}"])
    return builder.build()


def test_build_keeps_structure_and_leaves_id_empty() -> None:
    table = _table()
    assert table.id == ""
    assert [c.label for c in table.columns] == ["곡", "상수"]
    assert [g.title for g in table.groups] == ["15.5", "15.0"]
    assert table.groups[0].summary == {"평균": "15.5"}
    assert table.summary == {"닉네임": "유니"}
    assert table.steps == ("15 이상",)
    assert table.subject == "p1"
    assert table.unit == "채보"
    assert table.row_formats == ("{곡} {상수}",)
    assert table.row_count == 3


def test_table_is_frozen() -> None:
    table = _table()
    with pytest.raises(dataclasses.FrozenInstanceError):
        table.id = "t1"  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(TypeError):
        table.summary["x"] = "y"  # pyright: ignore[reportIndexIssue]
    with pytest.raises(TypeError):
        table.rows[0].cells["song"] = Cell()  # pyright: ignore[reportIndexIssue]


def test_row_without_group_gets_untitled_group() -> None:
    builder = TableBuilder().column("a")
    builder.row(RowRef("k", "1"), {"a": cell("x")})
    table = builder.build()
    assert len(table.groups) == 1
    assert table.groups[0].title is None


def test_cell_defaults() -> None:
    assert cell(None) == Cell(None, "")
    assert cell(1007500) == Cell(1007500, "1007500")
    assert cell(1007500, "1,007,500").text == "1,007,500"
    assert not cell(None, "점수 없음").filled
    assert not cell(5, "").filled
    assert cell(5, "5").filled


def test_missing_cells_read_as_empty() -> None:
    table = _table()
    last = table.rows[2]
    assert last.cell("const") == Cell()


def test_label_defaults_to_name() -> None:
    table = TableBuilder().column("score").build()
    assert table.columns[0].label == "score"


@pytest.mark.parametrize(
    "build",
    [
        lambda b: b.column("a").column("a", "b"),
        lambda b: b.column("a").column("b", "a"),
        lambda b: b.column("a", value_type="float"),  # type: ignore[arg-type]
        lambda b: b.column("a").row(RowRef("k", "1"), {"zzz": cell(1)}),
    ],
)
def test_builder_rejects_bad_input(build) -> None:
    with pytest.raises(TableError):
        build(TableBuilder())


def test_numbers_continue_across_groups() -> None:
    table = _table()
    assert [n for n, _ in table.numbered_rows()] == [1, 2, 3]
    assert [row.ref.key for _, row in table.numbered_rows()] == ["a", "b", "c"]
    assert row_at(table, 1).ref.key == "a"
    assert row_at(table, 3).ref.key == "c"


@pytest.mark.parametrize("number", [0, 4, -1])
def test_row_at_out_of_range(number: int) -> None:
    with pytest.raises(TableError, match="그 표는 3행까지 있다"):
        row_at(_table(), number)


def test_from_table_copies_everything_and_leaves_original() -> None:
    original = dataclasses.replace(_table(), id="t1")
    copied = (
        TableBuilder.from_table(original).step("더 거름").column("rank", "랭크").build()
    )
    assert copied.id == ""
    assert copied.row_formats == original.row_formats
    assert copied.subject == "p1"
    assert copied.unit == "채보"
    assert copied.summary == original.summary
    assert copied.groups == original.groups
    assert copied.steps == ("15 이상", "더 거름")
    assert [c.label for c in copied.columns] == ["곡", "상수", "랭크"]
    assert [c.label for c in original.columns] == ["곡", "상수"]
    assert original.steps == ("15 이상",)


def test_column_lookup() -> None:
    table = _table()
    column = table.column_by_label("상수")
    assert column is not None and column.name == "const"
    assert table.column_by_name("song") is not None
    assert table.column_by_label("없음") is None


# ---- TableStore ----


def _subject_table(subject: str | None = "p1") -> WorkTable:
    builder = (
        TableBuilder(subject=subject)
        .column("a")
        .row(RowRef("k", "1"), {"a": cell("x")})
    )
    return builder.build()


def test_store_put_numbers_per_player_and_channel() -> None:
    store = TableStore()
    assert store.put("p1", "c1", _subject_table(), now=0) == "t1"
    assert store.put("p1", "c1", _subject_table(), now=0) == "t2"
    assert store.put("p1", "c2", _subject_table(), now=0) == "t1"
    assert store.put("p2", "c1", _subject_table(), now=0) == "t1"
    stored = store.get("p1", "c1", "t2")
    assert stored is not None and stored.id == "t2"
    assert store.ids("p1", "c1") == ["t1", "t2"]


def test_store_does_not_leak_across_players_or_channels() -> None:
    store = TableStore()
    store.put("p1", "c1", _subject_table(), now=0)
    assert store.get("p2", "c1", "t1") is None
    assert store.get("p1", "c2", "t1") is None
    assert store.get("p1", "c1", "t9") is None


def test_store_does_not_reuse_numbers_after_delete() -> None:
    store = TableStore()
    store.put("p1", "c1", _subject_table(), now=0)
    store.retain_only("p1", "c1", [])
    assert store.get("p1", "c1", "t1") is None
    assert store.put("p1", "c1", _subject_table(), now=0) == "t2"


def test_store_does_not_change_the_table_given() -> None:
    store = TableStore()
    original = _subject_table()
    store.put("p1", "c1", original, now=0)
    assert original.id == ""


def test_retain_only_keeps_tables_bound_to_alive_messages() -> None:
    store = TableStore()
    for _ in range(4):
        store.put("p1", "c1", _subject_table(), now=0)
    store.put("p2", "c1", _subject_table(), now=0)
    store.bind("p1", "c1", "t1", "m1")
    store.bind("p1", "c1", "t2", "m2")
    store.bind("p1", "c1", "t3", "m3")  # t4는 묶이지 않았다
    store.retain_only("p1", "c1", {"m2", "m3", "m9"})
    assert store.ids("p1", "c1") == ["t2", "t3"]
    assert store.ids("p2", "c1") == ["t1"]


def test_table_for_message_and_last_bound() -> None:
    store = TableStore()
    for _ in range(3):
        store.put("p1", "c1", _subject_table(), now=0)
    assert store.last_bound("p1", "c1") is None
    store.bind("p1", "c1", "t2", "m2")
    store.bind("p1", "c1", "t1", "m1")
    assert store.table_for_message("p1", "c1", "m2") == "t2"
    assert store.table_for_message("p1", "c1", "m9") is None
    assert store.table_for_message("p1", "c2", "m2") is None
    assert (
        store.last_bound("p1", "c1") == "t1"
    )  # 가장 나중에 묶은 것 (id 순서가 아니다)
    store.bind("p1", "c1", "t9", "mx")  # 없는 표에 묶기는 무시한다
    assert store.last_bound("p1", "c1") == "t1"


def test_expire_removes_tables_older_than_24_hours() -> None:
    store = TableStore()
    store.put("p1", "c1", _subject_table(), now=0)
    store.put("p1", "c1", _subject_table(), now=1000)
    assert store.expire(TABLE_TTL_SECONDS - 1) == 0
    assert store.expire(TABLE_TTL_SECONDS) == 1
    assert store.ids("p1", "c1") == ["t2"]
    assert store.expire(1000 + TABLE_TTL_SECONDS) == 1
    assert store.ids("p1", "c1") == []


def test_mark_stale_only_marks_tables_of_that_subject_in_every_channel() -> None:
    store = TableStore()
    store.put("p1", "c1", _subject_table("p1"), now=0)
    store.put("p1", "c2", _subject_table("p1"), now=0)
    store.put("p1", "c1", _subject_table("p2"), now=0)  # 남의 데이터로 만든 표
    store.put(
        "p1", "c1", _subject_table(None), now=0
    )  # 곡 검색처럼 플레이어와 무관한 표
    store.mark_stale("p1")
    assert store.is_stale("p1", "c1", "t1")
    assert store.is_stale("p1", "c2", "t1")
    assert not store.is_stale("p1", "c1", "t2")
    assert not store.is_stale("p1", "c1", "t3")
    assert not store.is_stale("p1", "c1", "t9")
    assert store.get("p1", "c1", "t1") == dataclasses.replace(
        _subject_table("p1"), id="t1"
    )


def test_put_with_stale_flag_marks_new_table() -> None:
    store = TableStore()
    store.put("p1", "c1", _subject_table(), now=0, stale=True)
    store.put("p1", "c1", _subject_table(), now=0)
    assert store.is_stale("p1", "c1", "t1")
    assert not store.is_stale("p1", "c1", "t2")


# ---- 표 인자 해석 (TableRef) ----


def _chart_table(subject: str | None = "p1") -> WorkTable:
    """채보 3행 + 곡 단위 행 2개(난이도 없음) + 곡 없는 행 1개."""
    builder = TableBuilder(subject=subject, unit="행").column("song", "곡", bare=True).column("diff", "난이도")
    builder.group("A")
    builder.row(RowRef("chunithm.chart", "1"), {"song": cell("S1"), "diff": cell("MAS")})
    builder.row(RowRef("chunithm.chart", "2"), {"song": cell("S2"), "diff": cell("EXP")})
    builder.row(RowRef("chunithm.song", "3"), {"song": cell("S3"), "diff": cell(None)})
    builder.group("B", {"평균": "1"})
    builder.row(RowRef("chunithm.song", "4"), {"song": cell("S4"), "diff": cell(None)})
    builder.row(RowRef("goal", "5"), {"song": cell(None), "diff": cell(None)})
    builder.row(RowRef("chunithm.chart", "6"), {"song": cell("S6"), "diff": cell("MAS")})
    return builder.summary({"합": "6"}).step("원본").build()


_HINT = PartialRowHint(
    column="diff", given="song", label="곡 단위 행(난이도 없음)", advice="`charts_of`로 난이도를 정할 수 있다"
)


def _store_with(table: WorkTable, *, stale: bool = False) -> TableStore:
    store = TableStore()
    store.put("p1", "c1", table, now=0, stale=stale)
    return store


def _resolve(raw: object, store: TableStore | None, **kwargs: Any) -> TableArg:
    options: dict[str, Any] = {
        "store": store, "player_id": "p1", "channel_id": "c1", "needs": None, "mutates": False,
    }
    options.update(kwargs)
    return resolve_table_arg(raw, **options)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("t3", ("t3", None)), (" t3 ", ("t3", None)), ("t3:2,3", ("t3", [2, 3])), ("t3 : 2 , 3", ("t3", [2, 3])), ("t03:07", ("t3", [7]))],
)
def test_parse_table_arg(raw: str, expected: tuple[str, list[int] | None]) -> None:
    assert parse_table_arg(raw) == expected


@pytest.mark.parametrize("raw", ["", "3", "T3", "t", "t3:", "t3:2,", "t3:2-4", "t3:a", "t3,4", "t3:2:3", "표3"])
def test_parse_table_arg_rejects_bad_shape(raw: str) -> None:
    with pytest.raises(TableError, match="표 id 모양이 틀렸다"):
        parse_table_arg(raw)


def test_resolve_returns_the_stored_table() -> None:
    store = _store_with(_chart_table())
    arg = _resolve("t1", store)
    assert arg.table.id == "t1"
    assert arg.table.row_count == 6
    assert not arg.stale


def test_resolve_rejects_non_string() -> None:
    with pytest.raises(TableError, match="모양"):
        _resolve(3, _store_with(_chart_table()))


@pytest.mark.parametrize(
    "kwargs",
    [{"player_id": "p2"}, {"channel_id": "c2"}, {"channel_id": None}, {"store": None}],
)
def test_resolve_misses_other_player_or_channel(kwargs: dict[str, Any]) -> None:
    store = _store_with(_chart_table())
    with pytest.raises(TableError, match="그 표 없음"):
        _resolve("t1", kwargs.pop("store", store), **kwargs)
    with pytest.raises(TableError, match="그 표 없음"):
        _resolve("t7", _store_with(_chart_table()))


def test_resolve_rejects_stale_table_for_mutating_tool_only() -> None:
    store = _store_with(_chart_table(), stale=True)
    with pytest.raises(TableError, match="동기화 전 값이라 다시 만들어야 한다"):
        _resolve("t1", store, mutates=True)
    arg = _resolve("t1", store, mutates=False)
    assert arg.stale


def test_resolve_numbers_pick_the_same_rows_as_the_printed_numbers() -> None:
    store = _store_with(_chart_table())
    arg = _resolve("t1:2,6", store)
    assert [row.ref.key for row in arg.table.rows] == ["2", "6"]
    assert arg.table.id == ""
    assert arg.table.steps == ("원본", "2·6번")
    assert [g.title for g in arg.table.groups] == ["A", "B"]
    assert arg.table.summary == {}
    assert arg.table.groups[1].summary == {}


def test_resolve_numbers_drop_emptied_groups() -> None:
    arg = _resolve("t1:4", _store_with(_chart_table()))
    assert [g.title for g in arg.table.groups] == ["B"]


@pytest.mark.parametrize("raw", ["t1:7", "t1:0", "t1:1,9"])
def test_resolve_numbers_out_of_range(raw: str) -> None:
    with pytest.raises(TableError, match="그 표는 6행까지 있다"):
        _resolve(raw, _store_with(_chart_table()))


def test_resolve_numbers_rejected_on_table_made_this_turn() -> None:
    store = _store_with(_chart_table())
    with pytest.raises(TableError, match="번호는 사람이 본 표에만 쓴다"):
        _resolve("t1:2", store, turn_table_ids={"t1"})
    assert _resolve("t1", store, turn_table_ids={"t1"}).table.row_count == 6  # 번호 없이는 된다
    assert _resolve("t1:2", store, turn_table_ids={"t2"}).table.row_count == 1


def test_needs_ref_kind_drops_other_rows() -> None:
    arg = _resolve("t1", _store_with(_chart_table()), needs=TableNeeds(ref_kind="chunithm.chart"))
    assert [row.ref.key for row in arg.table.rows] == ["1", "2", "6"]
    assert arg.table.steps == ("원본", "대상 종류가 아닌 행 3개 뺌")
    assert arg.table.summary == {}


def test_needs_nonempty_drops_rows_and_names_the_reason() -> None:
    arg = _resolve("t1", _store_with(_chart_table()), needs=TableNeeds(nonempty=["song"]))
    assert [row.ref.key for row in arg.table.rows] == ["1", "2", "3", "4", "6"]
    assert arg.table.steps == ("원본", "곡 없는 행 1개 뺌")


def test_partial_rows_are_counted_apart_with_advice() -> None:
    needs = TableNeeds(nonempty=["song", "diff"])
    arg = _resolve("t1", _store_with(_chart_table()), needs=needs, partial_row_hints=[_HINT])
    assert [row.ref.key for row in arg.table.rows] == ["1", "2", "6"]
    assert arg.table.steps == (
        "원본",
        "곡 없는 행 1개 뺌",
        "곡 단위 행(난이도 없음) 2개 뺌 — `charts_of`로 난이도를 정할 수 있다",
    )


def test_partial_rows_without_registered_hint_use_general_wording() -> None:
    needs = TableNeeds(nonempty=["song", "diff"])
    arg = _resolve("t1", _store_with(_chart_table()), needs=needs)
    assert arg.table.steps == ("원본", "곡 없는 행 1개 뺌", "난이도 없는 행 2개 뺌")


def test_nothing_dropped_keeps_the_stored_table_as_is() -> None:
    store = _store_with(_chart_table())
    arg = _resolve("t1", store, needs=TableNeeds(columns=["song"]))
    assert arg.table.id == "t1"
    assert arg.table.summary == {"합": "6"}


def test_all_rows_dropped_is_an_error_naming_the_reasons() -> None:
    table = TableBuilder().column("song", "곡").column("diff", "난이도")
    table.row(RowRef("chunithm.song", "1"), {"song": cell("S"), "diff": cell(None)})
    table.row(RowRef("goal", "2"), {"song": cell(None), "diff": cell(None)})
    store = _store_with(table.build())
    with pytest.raises(TableError) as info:
        _resolve("t1", store, needs=TableNeeds(nonempty=["song", "diff"]), partial_row_hints=[_HINT])
    assert str(info.value) == (
        "대상 행이 없다(전부 2개 뺌: 곡 없는 행 1개 뺌; 곡 단위 행(난이도 없음) 1개 뺌 — `charts_of`로 난이도를 정할 수 있다)"
    )


def test_needs_columns_must_exist_in_table() -> None:
    with pytest.raises(TableError, match="'rank' 칸이 없다"):
        _resolve("t1", _store_with(_chart_table()), needs=TableNeeds(columns=["rank"]))


def test_single_needs_exactly_one_row() -> None:
    store = _store_with(_chart_table())
    needs = TableNeeds(single=True)
    with pytest.raises(TableError, match=r"한 행만 골라야 한다\(지금 6개\)"):
        _resolve("t1", store, needs=needs)
    assert _resolve("t1:3", store, needs=needs).table.row_count == 1
    # 필요한 것으로 빼고 난 뒤의 행 수로 센다
    with pytest.raises(TableError, match=r"지금 3개"):
        _resolve("t1", store, needs=TableNeeds(single=True, ref_kind="chunithm.chart"))
    assert _resolve("t1:1,3", store, needs=TableNeeds(single=True, ref_kind="chunithm.chart")).table.row_count == 1


def test_numbers_come_before_needs() -> None:
    # 3번은 곡 단위 행이라 필요한 것(난이도)에서 빠지고, 남는 것은 1번 하나다
    arg = _resolve("t1:1,3", _store_with(_chart_table()), needs=TableNeeds(nonempty=["diff"]))
    assert [row.ref.key for row in arg.table.rows] == ["1"]
    assert arg.table.steps[:2] == ("원본", "1·3번")


def test_table_needs_normalizes_sequences_to_tuples() -> None:
    needs = TableNeeds(columns=["a"], nonempty=["b", "c"])
    assert needs.columns == ("a",)
    assert needs.nonempty == ("b", "c")
    assert needs.ref_kind is None and needs.single is False
