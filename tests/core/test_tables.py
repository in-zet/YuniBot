from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from core.tables import (
    PEEK_MAX_ROWS,
    TABLE_FILE_ROWS,
    TABLE_TTL_SECONDS,
    Cell,
    Column,
    Condition,
    PartialRowHint,
    Row,
    RowRef,
    TableArg,
    TableBuilder,
    TableError,
    TableNeeds,
    TableStore,
    WorkTable,
    canonical_order_value,
    cell,
    describe_table,
    expand_rows,
    extend_table,
    parse_table_arg,
    peek_rows,
    render_text,
    resolve_table_arg,
    row_at,
    row_text,
    select_rows,
    summarize_table,
    table_file_name,
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


# ---- 모델에 주는 표 요약 ----


def test_summarize_table_gives_the_model_only_what_it_may_see() -> None:
    builder = TableBuilder(subject="p1", unit="채보").column("song", "곡").column("id", "아이디", slash_only=True)
    builder.group("15.5", {"평균": "15.5"}).row(RowRef("k", "secret"), {"song": cell("A"), "id": cell("g1")})
    builder.group("15.0").row(RowRef("k", "secret2"), {"song": cell("B"), "id": cell("g2")})
    table = dataclasses.replace(builder.summary({"레이팅": "16.98"}).steps(["15 이상", "내 점수"]).build(), id="t3")

    summary = summarize_table(table)

    assert summary == {
        "table": "t3", "rows": 2, "unit": "채보", "columns": ["곡"],
        "groups": [{"title": "15.5", "rows": 1, "summary": {"평균": "15.5"}}, {"title": "15.0", "rows": 1}],
        "steps": ["15 이상", "내 점수"], "summary": {"레이팅": "16.98"},
    }
    assert "secret" not in str(summary) and "p1" not in str(summary)
    assert summarize_table(table, stale=True)["stale"] is True


def test_summarize_table_omits_empty_parts() -> None:
    table = dataclasses.replace(TableBuilder().column("a").row(RowRef("k", "1"), {"a": cell("x")}).build(), id="t1")
    assert summarize_table(table) == {"table": "t1", "rows": 1, "unit": "개", "columns": ["a"]}


# ---- 거르기·정렬 엔진 (select_rows / peek_rows) ----


def _score_table() -> WorkTable:
    """상수 묶음 둘. 점수 없는 행 하나(5번)."""
    builder = TableBuilder(subject="p1", unit="채보")
    builder.column("song", "곡", bare=True).column("const", "상수", "number").column("score", "점수", "ScoreRef")
    builder.column("lamp", "램프", "LampRef").column("id", "아이디", slash_only=True)
    builder.group("15.5", {"평균": "x"})
    rows = [
        ("A", 15.5, 1_005_000, "FULL COMBO", "g1"),
        ("B", 15.5, 990_000, None, "g2"),
        ("C", 15.5, 1_008_000, "ALL JUSTICE", "g3"),
    ]
    for key, (song, const, score, lamp, gid) in enumerate(rows, start=1):
        builder.row(
            RowRef("chunithm.chart", str(key)),
            {
                "song": cell(song), "const": cell(const, f"{const}"), "score": cell(score, f"{score:,}"),
                "lamp": cell(lamp), "id": cell(gid),
            },
        )
    builder.group("15.0")
    builder.row(RowRef("chunithm.chart", "4"), {"song": cell("D"), "const": cell(15.0, "15.0"), "score": cell(1_000_000, "1,000,000"), "lamp": cell("FULL COMBO"), "id": cell("g4")})
    builder.row(RowRef("chunithm.chart", "5"), {"song": cell("E"), "const": cell(15.0, "15.0"), "score": cell(None, "점수 없음"), "lamp": cell(None), "id": cell("g5")})
    return builder.summary({"합": "1"}).step("원본").build()


def _songs(table: WorkTable) -> list[str]:
    return [str(row.cell("song").raw) for row in table.rows]


def _where(column: str, op: str, value: Any, text: str | None = None) -> Condition:
    return Condition(column=column, op=op, value=value, text=str(value) if text is None else text)


def test_where_compares_with_the_columns_value_and_missing_values_pass_only_below_or_not_equal() -> None:
    out = select_rows(_score_table(), where=[_where("점수", "<", 1_007_500, "SSS")])
    assert _songs(out) == ["A", "B", "D", "E"]  # 점수 없는 E는 통과, C(1,008,000)는 탈락
    assert out.steps == ("원본", "점수<SSS")
    assert out.id == ""


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        ("==", 1_000_000, ["D"]),
        ("!=", 1_000_000, ["A", "B", "C", "E"]),
        ("<=", 1_000_000, ["B", "D", "E"]),
        (">", 1_000_000, ["A", "C"]),
        (">=", 1_005_000, ["A", "C"]),
    ],
)
def test_where_operators(op: str, value: int, expected: list[str]) -> None:
    assert _songs(select_rows(_score_table(), where=[_where("점수", op, value)])) == expected


def test_where_conditions_are_anded_and_contains_works_on_text() -> None:
    table = _score_table()
    out = select_rows(table, where=[_where("상수", ">=", 15.5), _where("점수", ">", 1_000_000)])
    assert _songs(out) == ["A", "C"]
    assert _songs(select_rows(table, where=[_where("곡", "contains", "a")])) == ["A"]  # 대소문자 무시
    assert _songs(select_rows(table, where=[_where("램프", "==", "FULL COMBO")])) == ["A", "D"]


def test_where_errors() -> None:
    table = _score_table()
    with pytest.raises(TableError, match="'없음' 칸이 없다"):
        select_rows(table, where=[_where("없음", "==", 1)])
    with pytest.raises(TableError, match="모르는 비교"):
        select_rows(table, where=[_where("점수", "=~", 1)])
    with pytest.raises(TableError, match="크기를 비교할 수 없다"):
        select_rows(table, where=[_where("램프", ">", "FULL COMBO")])
    with pytest.raises(TableError, match="비교할 수 없다"):
        select_rows(table, where=[_where("점수", "<", "abc")])
    with pytest.raises(TableError, match="비교할 수 없다"):
        select_rows(table, where=[_where("점수", "contains", "1")])


def test_numbers_rows_and_exclude_rows_run_before_where() -> None:
    table = _score_table()
    assert _songs(select_rows(table, rows=[1, 3, 5])) == ["A", "C", "E"]
    assert _songs(select_rows(table, exclude_rows=[1])) == ["B", "C", "D", "E"]
    out = select_rows(table, rows=[1, 2, 3], exclude_rows=[2], where=[_where("점수", ">", 1_000_000)])
    assert _songs(out) == ["A", "C"]
    assert out.steps == ("원본", "1·2·3번", "2번 뺌", "점수>1000000")
    for bad in ([0], [6]):
        with pytest.raises(TableError, match="그 표는 5행까지 있다"):
            select_rows(table, rows=bad)
        with pytest.raises(TableError, match="그 표는 5행까지 있다"):
            select_rows(table, exclude_rows=bad)


def test_sort_is_within_groups_stable_and_missing_values_go_last_both_ways() -> None:
    table = _score_table()
    asc = select_rows(table, sort="점수", descending=False)
    assert _songs(asc) == ["B", "A", "C", "D", "E"]  # 묶음 안에서만 정렬, E(값 없음)는 D 뒤
    desc = select_rows(table, sort="점수", descending=True)
    assert _songs(desc) == ["C", "A", "B", "D", "E"]
    assert [g.title for g in desc.groups] == ["15.5", "15.0"]
    assert desc.steps == ("원본", "점수 내림차순")
    ties = select_rows(table, sort="상수")  # 같은 값이면 원래 순서
    assert _songs(ties) == ["A", "B", "C", "D", "E"]


def test_limit_applies_per_group() -> None:
    out = select_rows(_score_table(), sort="점수", descending=True, limit=2)
    assert _songs(out) == ["C", "A", "D", "E"]
    assert out.steps[-1] == "묶음마다 앞 2개"
    with pytest.raises(TableError, match="limit"):
        select_rows(_score_table(), limit=0)


def test_group_by_regroups_by_value_descending_with_shown_text_as_title() -> None:
    out = select_rows(_score_table(), group_by="상수", sort="점수", descending=True)
    assert [g.title for g in out.groups] == ["15.5", "15.0"]
    assert [_songs(dataclasses.replace(out, groups=(g,))) for g in out.groups] == [["C", "A", "B"], ["D", "E"]]
    assert all(not g.summary for g in out.groups)
    assert out.summary == {}
    by_lamp = select_rows(_score_table(), group_by="램프")
    assert [g.title for g in by_lamp.groups] == ["FULL COMBO", "ALL JUSTICE", "(없음)"]  # 값 내림차순, 없는 값은 맨 뒤


def test_group_by_empty_string_flattens_the_groups() -> None:
    out = select_rows(_score_table(), group_by="")
    assert len(out.groups) == 1 and out.groups[0].title is None
    assert _songs(out) == ["A", "B", "C", "D", "E"]
    assert out.steps[-1] == "묶음 풀기"


def test_filtering_keeps_group_titles_but_drops_summaries_and_empty_groups() -> None:
    table = _score_table()
    out = select_rows(table, where=[_where("상수", "==", 15.5)])
    assert [g.title for g in out.groups] == ["15.5"]
    assert out.groups[0].summary == {}
    assert out.summary == {}
    assert table.groups[0].summary == {"평균": "x"}  # 원래 표는 그대로


def test_select_rows_keeps_columns_formats_subject_and_unit() -> None:
    table = dataclasses.replace(_score_table(), row_formats=("{곡}",))
    out = select_rows(table, rows=[1])
    assert out.columns == table.columns and out.row_formats == ("{곡}",)
    assert out.subject == "p1" and out.unit == "채보"


def test_peek_rows_returns_raw_values_with_original_numbers_and_hides_slash_only() -> None:
    table = dataclasses.replace(_score_table(), id="t3")
    data = peek_rows(table, rows=[3, 5])
    assert data == {
        "table": "t3",
        "matched": 2,
        "rows": [
            {"number": 3, "values": {"곡": "C", "상수": 15.5, "점수": 1_008_000, "램프": "ALL JUSTICE"}},
            {"number": 5, "values": {"곡": "E", "상수": 15.0, "점수": None, "램프": None}},
        ],
    }


def test_peek_rows_where_sort_and_limit() -> None:
    table = _score_table()
    data = peek_rows(table, where=[_where("상수", "==", 15.5)], sort="점수", descending=True, limit=2)
    assert [r["number"] for r in data["rows"]] == [3, 1]
    assert data["matched"] == 3
    # 정렬은 묶음과 무관하게 전체에서 한다
    top = peek_rows(table, sort="점수", descending=True, limit=1)
    assert top["rows"][0]["number"] == 3
    assert len(peek_rows(table, limit=999)["rows"]) == 5  # 상한 20 안에서 전부
    assert len(peek_rows(table, limit=0)["rows"]) == 1  # 최소 1


def test_peek_rows_caps_at_twenty() -> None:
    builder = TableBuilder().column("a")
    for i in range(30):
        builder.row(RowRef("k", str(i)), {"a": cell(i)})
    data = peek_rows(builder.build(), limit=100)
    assert len(data["rows"]) == PEEK_MAX_ROWS and data["matched"] == 30


def test_peek_rows_rejects_group_by_and_exclude() -> None:
    with pytest.raises(TableError):
        peek_rows(_score_table(), group_by="상수")
    with pytest.raises(TableError):
        peek_rows(_score_table(), exclude_rows=[1])


# ---- 글자 출력 (row_text / render_text) ----


def _fmt_table(*, formats: tuple[str, ...] = (), slash_only: bool = True) -> WorkTable:
    builder = TableBuilder(unit="채보")
    builder.column("song", "곡", bare=True).column("diff", "난이도", bare=True).column("const", "상수")
    builder.column("score", "점수").column("goal", "목표 점수").column("left", "남은 점수")
    builder.column("pub", "공개", hidden_in_text=True)
    if slash_only:
        builder.column("id", "아이디", slash_only=True)
    cells = {
        "song": cell("크로노"), "diff": cell("MAS"), "const": cell(15.2, "15.2"), "score": cell(1_005_000, "1,005,000"),
        "goal": cell(1_007_500, "1,007,500"), "left": cell(2_500, "2,500"), "pub": cell(True, "공개"),
    }
    if slash_only:
        cells["id"] = cell("g7")
    builder.row(RowRef("k", "1"), cells)
    builder.row(RowRef("k", "2"), {"song": cell("모모"), "diff": cell("EXP"), "const": cell(14.0, "14.0")})
    return dataclasses.replace(builder.build(), row_formats=formats)


def test_row_text_default_output_names_columns_and_skips_empty_ones() -> None:
    table = _fmt_table()
    first, second = table.rows
    assert row_text(table, first) == "크로노  MAS  상수 15.2  점수 1,005,000  목표 점수 1,007,500  남은 점수 2,500"
    assert row_text(table, second) == "모모  EXP  상수 14.0"  # 비어 있는 칸은 `-` 없이 뺀다


def test_row_text_hides_hidden_in_text_and_shows_slash_only_only_for_slash() -> None:
    table = _fmt_table()
    first = table.rows[0]
    assert "공개" not in row_text(table, first) and "g7" not in row_text(table, first)
    assert row_text(table, first, slash=True).endswith("아이디 g7")
    assert "공개" not in row_text(table, first, slash=True)


FORMATS = ("{곡} {난이도} {상수}  {점수} → {목표 점수}  (남은 {남은 점수}점)", "{곡} {난이도} {상수}")


def test_row_text_uses_first_format_whose_columns_are_all_filled() -> None:
    table = _fmt_table(formats=FORMATS)
    first, second = table.rows
    assert row_text(table, first) == "크로노 MAS 15.2  1,005,000 → 1,007,500  (남은 2,500점)"
    assert row_text(table, second) == "모모 EXP 14.0"  # 첫 서식은 칸이 비어 맞지 않아 둘째 서식


def test_row_text_appends_columns_missing_from_the_format() -> None:
    table = _fmt_table(formats=("{곡} {난이도}",))
    assert row_text(table, table.rows[0]) == (
        "크로노 MAS  상수 15.2  점수 1,005,000  목표 점수 1,007,500  남은 점수 2,500"
    )


def test_row_text_falls_back_to_default_when_no_format_fits_or_names_unknown_column() -> None:
    table = _fmt_table(formats=("{곡} {없는 칸}", "{곡} {점수}"))
    second = table.rows[1]
    assert row_text(table, second) == "모모  EXP  상수 14.0"  # 맞는 서식이 없으면 기본 출력
    assert row_text(table, table.rows[0]).startswith("크로노 1,005,000")


def test_row_text_format_cannot_use_columns_hidden_from_this_output() -> None:
    table = _fmt_table(formats=("{곡} {아이디}", "{곡}"))
    assert row_text(table, table.rows[0]).startswith("크로노  ")  # 슬래시가 아니면 {아이디}가 있는 서식은 건너뛴다
    assert row_text(table, table.rows[0], slash=True).startswith("크로노 g7")


def _numbered_table(count: int, *, titled: bool = True, steps: tuple[str, ...] = ("15.0 상수 MAS",)) -> WorkTable:
    builder = TableBuilder(unit="채보").column("song", "곡", bare=True)
    builder.group("A" if titled else None, {"평균": "1"} if titled else None)
    for i in range(1, count + 1):
        if i == 3:
            builder.group("B")
        builder.row(RowRef("k", str(i)), {"song": cell(f"S{i}")})
    return builder.summary({"레이팅": "16.98"}).steps(steps).build()


def test_render_text_header_rules_and_numbers_continue_across_groups() -> None:
    rendered = render_text(_numbered_table(4))
    assert rendered.file is None
    assert rendered.content.split("\n") == [
        "15.0 상수 MAS",
        "레이팅 16.98",
        "── A · 2채보 · 평균 1 ──",
        "1. S1",
        "2. S2",
        "── B · 2채보 ──",
        "3. S3",
        "4. S4",
    ]


def test_render_text_joins_steps_with_arrows_and_marks_stale() -> None:
    rendered = render_text(_numbered_table(1, steps=("15 이상", "내 점수", "점수<SSS")), stale=True)
    assert rendered.content.split("\n")[:2] == ["(동기화 전 값)", "15 이상 → 내 점수 → 점수<SSS"]


def test_render_text_omits_the_rule_line_for_one_untitled_group() -> None:
    builder = TableBuilder().column("a", bare=True).row(RowRef("k", "1"), {"a": cell("x")})
    assert render_text(builder.build()).content == "1. x"


def test_render_text_rule_line_for_untitled_group_among_others() -> None:
    builder = TableBuilder(unit="곡").column("a", bare=True)
    builder.group(None).row(RowRef("k", "1"), {"a": cell("x")})
    builder.group("B").row(RowRef("k", "2"), {"a": cell("y")})
    assert render_text(builder.build()).content.split("\n")[0] == "── 1곡 ──"


def test_render_text_ten_rows_stay_in_the_message_and_eleven_go_to_a_file() -> None:
    inline = render_text(_numbered_table(TABLE_FILE_ROWS))
    assert inline.file is None and "10. S10" in inline.content

    rendered = render_text(_numbered_table(TABLE_FILE_ROWS + 1, titled=False))
    assert rendered.file is not None
    name, data = rendered.file
    assert name == "15.0상수MAS.txt"
    assert "1. S1" not in rendered.content  # 메시지에는 머리말만
    assert rendered.content.split("\n") == ["15.0 상수 MAS", "레이팅 16.98", "11채보 — 목록은 첨부 파일(15.0상수MAS.txt)"]
    text = data.decode("utf-8")
    assert text.split("\n")[:2] == ["15.0 상수 MAS", "레이팅 16.98"]
    assert "11. S11" in text and "1. S1\n" in text


def test_table_file_name_falls_back_to_table_txt() -> None:
    assert table_file_name(_numbered_table(1, steps=())) == "table.txt"
    assert table_file_name(_numbered_table(1, steps=("★ / ?",))) == "table.txt"
    assert table_file_name(_numbered_table(1, steps=("점수<SSS",))) == "점수SSS.txt"


def test_render_text_slash_shows_slash_only_columns() -> None:
    table = _fmt_table()
    assert "아이디 g7" not in render_text(table).content
    assert "아이디 g7" in render_text(table, slash=True).content


def test_numbers_in_text_file_and_rows_point_at_the_same_rows() -> None:
    table = _numbered_table(TABLE_FILE_ROWS + 3, titled=False)
    file = render_text(table).file
    assert file is not None
    data = file[1]
    lines = [line for line in data.decode("utf-8").split("\n") if line[:1].isdigit() and ". " in line]
    for line in lines:
        number = int(line.split(".", 1)[0])
        assert line == f"{number}. {row_text(table, row_at(table, number))}"


def test_discard_and_drop_channel() -> None:
    store = TableStore()
    for player, channel in [("p1", "c1"), ("p1", "c1"), ("p2", "c1"), ("p1", "c2")]:
        _ = store.put(player, channel, _subject_table(), now=0)
    store.discard("p1", "c1", "t1")
    store.discard("p1", "c1", "t9")  # 없는 표는 무시한다
    assert store.ids("p1", "c1") == ["t2"]
    store.drop_channel("c1")
    assert store.ids("p1", "c1") == [] and store.ids("p2", "c1") == []
    assert store.ids("p1", "c2") == ["t1"]


def test_describe_table_lists_steps_unit_and_visible_columns_only() -> None:
    builder = TableBuilder(unit="채보").column("song", "곡").column("id", "아이디", slash_only=True)
    builder.row(RowRef("k", "1"), {"song": cell("A"), "id": cell("g")})
    table = dataclasses.replace(builder.steps(["15 이상", "내 점수"]).build(), id="t3")
    assert describe_table(table) == "[살아 있는 표] t3 — 15 이상 → 내 점수 (1채보, 칸: 곡)"
    assert describe_table(table, stale=True).startswith("[살아 있는 표] t3 (동기화 전 값) — ")
    bare = dataclasses.replace(TableBuilder().column("a").build(), id="t1")
    assert "과정 없음" in describe_table(bare)


def test_extend_table_adds_columns_cells_and_keeps_rows_groups_and_format() -> None:
    source = _table()

    extended = extend_table(
        source,
        [Column(name="score", label="점수", value_type="ScoreRef")],
        lambda row: {"score": cell(1, "1")} if row.ref.key != "c" else {},
        steps=["내 점수"],
    )

    assert [c.name for c in extended.columns] == ["song", "const", "score"]
    assert [g.title for g in extended.groups] == ["15.5", "15.0"]
    assert [row.ref for row in extended.rows] == [row.ref for row in source.rows]
    assert extended.rows[0].cell("score").raw == 1 and not extended.rows[2].cell("score").filled
    assert extended.steps == ("15 이상", "내 점수")
    assert extended.row_formats == source.row_formats and extended.subject == "p1"
    assert extended.id == "" and not extended.summary and not extended.groups[0].summary
    assert source.column_by_name("score") is None


def test_expand_rows_replaces_keeps_and_drops_rows_and_adds_columns() -> None:
    source = _table()
    first, second = source.rows[0], source.rows[1]

    def expand(row: Row) -> list[Row] | None:
        if row is first:
            return [Row(RowRef("k", "a1"), {"song": cell("A1")}), Row(RowRef("k", "a2"), {"song": cell("A2")})]
        if row is second:
            return []
        return None

    result = expand_rows(source, expand, [Column(name="level", label="레벨")], steps=["펼침"])

    assert [row.ref.key for row in result.rows[:3]] == ["a1", "a2", source.rows[2].ref.key]
    assert result.row_count == source.row_count + 1 - 1
    assert [c.name for c in result.columns] == ["song", "const", "level"]
    assert result.steps == ("15 이상", "펼침") and result.id == "" and result.subject == "p1"
    assert source.row_count == len(source.rows) and source.column_by_name("level") is None


def test_extend_table_overwrites_existing_column_cells_and_rejects_label_clash() -> None:
    source = _table()

    overwritten = extend_table(
        source, [Column(name="const", label="상수", value_type="number")], lambda _row: {"const": cell(1.0, "1.0")}
    )
    assert [c.name for c in overwritten.columns] == ["song", "const"]
    assert all(row.cell("const").raw == 1.0 for row in overwritten.rows)

    with pytest.raises(TableError):
        extend_table(source, [Column(name="other", label="상수")], lambda _row: {})


# ---- 순서가 있는 칸 · 묶음 순서 · 보일 칸 고르기 ----

_VERSIONS = ("OLD", "MID", "NEW")


def _version_table() -> WorkTable:
    """글 칸 버전(OLD<MID<NEW)과 작곡가·곡. 버전 순서는 일부러 알파벳순과 다르다(MID, NEW, OLD가 알파벳순)."""
    builder = TableBuilder(unit="채보")
    builder.column("song", "곡", bare=True).column("artist", "작곡가").column("const", "상수", "number")
    builder.column("version", "버전", order=_VERSIONS)
    rows = [
        ("a", "X", 15.0, "OLD"),
        ("b", "Y", 15.5, "NEW"),
        ("c", "X", 15.2, "MID"),
        ("d", "Z", 14.8, "NEW"),
        ("e", "Y", 15.1, "OLD"),
        ("f", "Z", 15.3, "ZZZ"),  # 순서 목록에 없는 새 버전
    ]
    for key, (song, artist, const, version) in enumerate(rows, start=1):
        builder.row(
            RowRef("chunithm.chart", str(key)),
            {"song": cell(song), "artist": cell(artist), "const": cell(const, f"{const}"), "version": cell(version)},
        )
    return builder.build()


def test_ordered_text_column_sorts_by_its_order_not_alphabetically() -> None:
    table = _version_table()
    newest_first = select_rows(table, sort="버전", descending=True)
    assert _songs(newest_first) == ["b", "d", "c", "a", "e", "f"]  # NEW, NEW, MID, OLD, OLD, 목록에 없는 값은 뒤
    oldest_first = select_rows(table, sort="버전", descending=False)
    assert _songs(oldest_first) == ["a", "e", "c", "b", "d", "f"]


def test_ordered_text_column_compares_by_order_and_unknown_values_never_match_a_size_compare() -> None:
    table = _version_table()
    assert _songs(select_rows(table, where=[_where("버전", ">=", "MID")])) == ["b", "c", "d"]
    assert _songs(select_rows(table, where=[_where("버전", "<", "MID")])) == ["a", "e"]
    assert _songs(select_rows(table, where=[_where("버전", "==", "NEW")])) == ["b", "d"]
    assert _songs(select_rows(table, where=[_where("버전", "contains", "ew")])) == ["b", "d"]


def test_sort_direction_defaults_to_biggest_first_for_number_and_ordered_columns_and_alphabetical_for_text() -> None:
    """방향을 안 정하면(2026-10-22 사용자 결정) 숫자·순서가 있는 칸은 큰 쪽부터, 글 칸은 가나다순이다."""
    table = _version_table()
    assert _songs(select_rows(table, sort="상수")) == ["b", "f", "c", "e", "a", "d"]
    assert select_rows(table, sort="상수").steps[-1] == "상수 내림차순"
    assert _songs(select_rows(table, sort="버전")) == ["b", "d", "c", "a", "e", "f"]
    assert _songs(select_rows(table, sort="곡")) == ["a", "b", "c", "d", "e", "f"]
    assert select_rows(table, sort="곡").steps[-1] == "곡 오름차순"
    assert _songs(select_rows(table, sort="상수", descending=False)) == ["d", "a", "e", "c", "f", "b"]  # 말하면 그대로
    assert _songs(select_rows(table, sort="곡", descending=True)) == ["f", "e", "d", "c", "b", "a"]
    assert [r["values"]["곡"] for r in peek_rows(table, sort="상수", limit=2)["rows"]] == ["b", "f"]


def test_sorting_the_sorted_result_again_stacks_and_keeps_the_earlier_order_among_ties() -> None:
    """정렬 기준이 둘이면 덜 중요한 기준으로 먼저 정렬하고, 그 결과 표를 더 중요한 기준으로 다시 정렬한다."""
    builder = TableBuilder(unit="채보")
    builder.column("song", "곡", bare=True).column("const", "상수", "number").column("version", "버전", order=_VERSIONS)
    for key, (song, const, version) in enumerate(
        [("a", 15.0, "OLD"), ("b", 15.0, "NEW"), ("c", 15.5, "MID"), ("d", 15.0, "MID")], start=1
    ):
        builder.row(
            RowRef("chunithm.chart", str(key)),
            {"song": cell(song), "const": cell(const, f"{const}"), "version": cell(version)},
        )
    table = builder.build()

    stacked = select_rows(select_rows(table, sort="버전"), sort="상수")

    assert _songs(stacked) == ["c", "b", "d", "a"]  # 상수 큰 순, 같은 상수 안에서는 버전 큰 순
    separate = select_rows(table, sort="상수")  # 같은 원래 표에 따로 걸면 앞 정렬이 쌓이지 않는다
    assert _songs(separate) == ["c", "a", "b", "d"]


def test_canonical_order_value_fixes_case_and_rejects_unknown_names() -> None:
    column = _version_table().columns[3]
    assert canonical_order_value(column, " mid ") == "MID"
    with pytest.raises(TableError, match="모른다"):
        canonical_order_value(column, "MIDDLE")


def test_group_by_ordered_column_puts_the_newest_group_first_and_sort_on_it_follows_descending() -> None:
    table = _version_table()
    by_version = select_rows(table, group_by="버전")
    assert [g.title for g in by_version.groups] == ["NEW", "MID", "OLD", "ZZZ"]  # 목록에 없는 값은 맨 뒤
    ascending = select_rows(table, group_by="버전", sort="버전", descending=False)
    assert [g.title for g in ascending.groups] == ["OLD", "MID", "NEW", "ZZZ"]
    descending = select_rows(table, group_by="버전", sort="버전", descending=True)
    assert [g.title for g in descending.groups] == ["NEW", "MID", "OLD", "ZZZ"]


def test_group_by_text_column_keeps_the_sorted_order_of_first_appearance() -> None:
    table = _version_table()
    # 작곡가(글 칸)로 묶으면서 상수 내림차순: 상수 높은 곡을 쓴 작곡가 묶음이 위로, 묶음 안 행도 같은 순서다.
    out = select_rows(table, group_by="작곡가", sort="상수", descending=True)
    assert [g.title for g in out.groups] == ["Y", "Z", "X"]
    assert _songs(out) == ["b", "e", "f", "d", "c", "a"]
    # 정렬이 없으면 표에 나온 순서(알파벳 역순이 아니다).
    assert [g.title for g in select_rows(table, group_by="작곡가").groups] == ["X", "Y", "Z"]


def test_group_by_after_a_separate_sort_keeps_that_order() -> None:
    table = _version_table()
    flat = select_rows(table, sort="버전", descending=True, group_by="")
    by_song_artist = select_rows(flat, group_by="작곡가")
    assert [g.title for g in by_song_artist.groups] == ["Y", "Z", "X"]  # 버전 내림차순에서 먼저 나온 순서


def test_columns_hides_the_other_columns_without_dropping_them() -> None:
    table = _version_table()
    out = select_rows(table, sort="버전", descending=True, columns=["곡"])
    assert out.steps[-1] == "곡만 보임"
    assert [column.name for column in out.columns] == [column.name for column in table.columns]
    assert [column.name for column in out.columns if not column.hidden_in_text] == ["song"]
    assert render_text(out).content.splitlines()[-1] == "6. f"
    # 숨긴 칸은 이어서 거르거나 정렬하는 데 그대로 쓰인다.
    assert _songs(select_rows(out, where=[_where("상수", ">", 15.1)])) == ["b", "c", "f"]
    # 다시 고르면 숨김이 바뀐다.
    again = select_rows(out, columns=["곡", "상수"])
    assert [column.name for column in again.columns if not column.hidden_in_text] == ["song", "const"]


def test_columns_errors() -> None:
    table = _score_table()
    with pytest.raises(TableError, match="하나는 골라야"):
        select_rows(table, columns=[])
    with pytest.raises(TableError, match="'없음' 칸이 없다"):
        select_rows(table, columns=["곡", "없음"])
    with pytest.raises(TableError, match="'아이디' 칸이 없다"):
        select_rows(table, columns=["아이디"])  # 슬래시 전용 칸은 모델에게 보이지 않는다


def test_distinct_by_keeps_the_first_row_of_the_sorted_order_across_groups() -> None:
    table = _version_table()
    out = select_rows(table, sort="상수", descending=True, distinct_by="작곡가")
    assert _songs(out) == ["b", "f", "c"]  # 작곡가 Y·Z·X마다 상수 제일 높은 곡
    assert out.steps[-1] == "작곡가마다 첫 행만"
    grouped = select_rows(table, group_by="작곡가", distinct_by="버전")
    assert _songs(grouped) == ["a", "c", "b", "f"]  # 버전이 겹치는 행(e: OLD, d: NEW)은 묶음을 넘어서도 빠진다


def test_distinct_by_never_merges_rows_without_a_value_and_runs_before_limit() -> None:
    table = _score_table()
    assert _songs(select_rows(table, distinct_by="램프")) == ["A", "B", "C", "E"]  # B·E는 램프 값이 없어 겹치지 않는다
    out = select_rows(table, sort="점수", descending=True, distinct_by="램프", limit=2)
    assert _songs(out) == ["C", "A", "E"]  # C(AJ)·A(FC)가 먼저 남고 D(FC)는 A와 겹쳐 빠진 뒤에 묶음마다 2개


def test_ordered_ref_column_sorts_and_compares_by_its_order_not_alphabetically() -> None:
    names = ("BASIC", "ADVANCED", "EXPERT", "MASTER", "ULTIMA")
    builder = TableBuilder().column("song", "곡", bare=True).column("diff", "난이도", "DifficultyRef", order=names)
    for key, diff in enumerate(["MASTER", "ULTIMA", "EXPERT", "BASIC", None], start=1):
        builder.row(RowRef("k", str(key)), {"song": cell(f"s{key}"), "diff": cell(diff)})
    table = builder.build()
    assert _songs(select_rows(table, sort="난이도", descending=True)) == ["s2", "s1", "s3", "s4", "s5"]  # ULTIMA, MASTER, EXPERT, BASIC, 없음
    assert _songs(select_rows(table, where=[_where("난이도", ">=", "MASTER")])) == ["s1", "s2"]  # 값 없는 행은 "이상"에 탈락
    assert [g.title for g in select_rows(table, group_by="난이도").groups] == ["ULTIMA", "MASTER", "EXPERT", "BASIC", "(없음)"]


def test_where_drops_rows_without_a_value_for_conditions_that_need_a_value() -> None:
    table = _score_table()  # E는 점수 없음
    for op, value in ((">=", 1_000_000), (">", 0), ("==", 1_000_000)):
        assert "E" not in _songs(select_rows(table, where=[_where("점수", op, value)])), op
    assert "E" not in _songs(select_rows(table, where=[_where("램프", "contains", "FULL")]))
    assert "E" in _songs(select_rows(table, where=[_where("점수", "<=", 1_000_000)]))  # "못 한 것"에는 안 친 채보가 든다
    assert "E" in _songs(select_rows(table, where=[_where("점수", "!=", 1_000_000)]))


def _rating_table() -> WorkTable:
    builder = TableBuilder(subject="p1", unit="채보")
    builder.column("song", "곡", bare=True).column("rating", "곡 레이팅", "number")
    values = [("A", 16.8341), ("B", 16.89), ("C", 16.9), ("D", 16.81), ("E", 17.05), ("F", None)]
    for key, (song, rating) in enumerate(values, start=1):
        builder.row(
            RowRef("chunithm.chart", str(key)),
            {"song": cell(song), "rating": cell(rating, "-" if rating is None else f"{rating:.2f}")},
        )
    return builder.build()


def test_group_step_groups_by_interval_biggest_first_and_sorts_inside() -> None:
    out = select_rows(
        _rating_table(), where=[_where("곡 레이팅", ">=", 16.8)], group_by="곡 레이팅", group_step="0.1",
        sort="곡 레이팅", descending=True,
    )

    assert [(g.title, [str(r.cell("song").raw) for r in g.rows]) for g in out.groups] == [
        ("17.00~17.09", ["E"]),
        ("16.90~16.99", ["C"]),
        ("16.80~16.89", ["B", "A", "D"]),  # 안은 곡 레이팅 내림차순
    ]
    assert "F" not in _songs(out)  # 값 없는 행은 "이상"에서 이미 탈락
    assert out.steps[-2] == "곡 레이팅 0.1 단위 묶음"


def test_group_step_puts_rows_without_a_value_last() -> None:
    out = select_rows(_rating_table(), group_by="곡 레이팅", group_step="0.1")

    assert [g.title for g in out.groups][-1] == "(없음)"


def test_group_step_with_integer_step_names_the_whole_range() -> None:
    out = select_rows(_score_table(), group_by="점수", group_step="10000")

    assert [g.title for g in out.groups] == ["1,000,000~1,009,999", "990,000~999,999", "(없음)"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"group_by": "곡", "group_step": "0.1"}, "구간으로 묶을 수 없다"),
        ({"group_by": "곡 레이팅", "group_step": "abc"}, "숫자가 아니다"),
        ({"group_by": "곡 레이팅", "group_step": "0"}, "0보다 큰"),
        ({"group_by": "곡 레이팅", "group_step": "NaN"}, "0보다 큰"),
        ({"group_by": "곡 레이팅", "group_step": "Infinity"}, "0보다 큰"),
        ({"group_by": "", "group_step": "0.1"}, "칸 이름을 줄 때만"),
        ({"group_step": "0.1"}, "칸 이름을 줄 때만"),
    ],
)
def test_group_step_rejects_bad_use(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(TableError, match=message):
        select_rows(_rating_table(), **kwargs)
