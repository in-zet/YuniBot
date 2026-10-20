import enum
from typing import Annotated

import pytest

from core.schema import SchemaBuildError, build_arg_schema
from core.tables import TableNeeds
from core.tool_types import ChartRef, TableRef, ToolContext


class Difficulty(enum.Enum):
    MAS = "MAS"
    ULT = "ULT"


async def simple(ctx: ToolContext, name: str, count: int = 3) -> None: ...
async def with_ref(ctx: ToolContext, chart: ChartRef, rank: str | None = None) -> None: ...
async def with_enum(ctx: ToolContext, difficulty: Difficulty) -> None: ...
async def unsupported(ctx: ToolContext, blob: dict[str, object]) -> None: ...
async def required_optional(ctx: ToolContext, x: str | None) -> None: ...
async def optional_enum(ctx: ToolContext, difficulty: Difficulty | None = None) -> None: ...
async def optional_unsupported(ctx: ToolContext, blob: int | str | None) -> None: ...


def test_build_arg_schema_primitives() -> None:
    schema = build_arg_schema(simple)
    assert [a.name for a in schema.args] == ["name", "count"]
    assert schema.args[0].required is True
    assert schema.args[1].required is False
    assert schema.args[1].default == 3


def test_build_arg_schema_ref_type_and_optional() -> None:
    schema = build_arg_schema(with_ref)
    chart_arg, rank_arg = schema.args
    assert chart_arg.is_ref_type is True
    assert chart_arg.py_type is ChartRef
    assert chart_arg.required is True
    assert rank_arg.required is False
    assert rank_arg.is_ref_type is False


def test_build_arg_schema_enum() -> None:
    schema = build_arg_schema(with_enum)
    assert schema.args[0].py_type is Difficulty


def test_build_arg_schema_required_optional_primitive() -> None:
    schema = build_arg_schema(required_optional)
    assert schema.args[0].py_type is str
    assert schema.args[0].required is False


def test_build_arg_schema_optional_enum() -> None:
    schema = build_arg_schema(optional_enum)
    assert schema.args[0].py_type is Difficulty
    assert schema.args[0].required is False


def test_build_arg_schema_rejects_optional_multi_member_union() -> None:
    with pytest.raises(SchemaBuildError, match="blob"):
        build_arg_schema(optional_unsupported)


def test_build_arg_schema_rejects_unsupported_type() -> None:
    with pytest.raises(SchemaBuildError, match="blob"):
        build_arg_schema(unsupported)


async def with_dry_run(ctx: ToolContext, n: int, *, dry_run: bool = False) -> None: ...


def test_build_arg_schema_excludes_core_injected_dry_run() -> None:
    """dry_run은 confirm="bulk" 도구에 실행기가 넣는 인자다 — 모델/슬래시 스키마에 나오지 않는다."""
    schema = build_arg_schema(with_dry_run)
    assert [a.name for a in schema.args] == ["n"]


_NEEDS = TableNeeds(ref_kind="chunithm.chart", nonempty=["song", "diff"])


async def with_table(ctx: ToolContext, table: Annotated[TableRef, _NEEDS], note: str = "") -> None: ...
async def with_bare_table(ctx: ToolContext, table: TableRef) -> None: ...
async def with_optional_table(ctx: ToolContext, table: Annotated[TableRef, _NEEDS] | None = None) -> None: ...
async def needs_on_wrong_type(ctx: ToolContext, name: Annotated[str, _NEEDS]) -> None: ...


def test_table_ref_is_a_ref_type_with_declared_needs() -> None:
    table_arg, note_arg = build_arg_schema(with_table).args
    assert table_arg.py_type is TableRef
    assert table_arg.is_ref_type is True
    assert table_arg.table_needs == _NEEDS
    assert note_arg.table_needs is None


def test_table_ref_without_needs_and_optional_table_ref() -> None:
    assert build_arg_schema(with_bare_table).args[0].table_needs is None
    (optional_arg,) = build_arg_schema(with_optional_table).args
    assert optional_arg.py_type is TableRef
    assert optional_arg.required is False
    assert optional_arg.table_needs == _NEEDS


def test_table_needs_on_another_type_is_rejected() -> None:
    with pytest.raises(SchemaBuildError, match="TableNeeds"):
        build_arg_schema(needs_on_wrong_type)
