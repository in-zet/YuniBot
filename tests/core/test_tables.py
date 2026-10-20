from __future__ import annotations

import dataclasses

import pytest

from core.tables import (
    Cell,
    RowRef,
    TableBuilder,
    TableError,
    WorkTable,
    cell,
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
