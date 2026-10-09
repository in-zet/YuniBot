import enum

import pytest

from core.schema import SchemaBuildError, build_arg_schema
from core.tool_types import ChartRef, ToolContext


class Difficulty(enum.Enum):
    MAS = "MAS"
    ULT = "ULT"


async def simple(ctx: ToolContext, name: str, count: int = 3) -> None: ...
async def with_ref(ctx: ToolContext, chart: ChartRef, rank: str | None = None) -> None: ...
async def with_enum(ctx: ToolContext, difficulty: Difficulty) -> None: ...
async def unsupported(ctx: ToolContext, blob: dict[str, object]) -> None: ...


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
    assert rank_arg.required is False
    assert rank_arg.is_ref_type is False


def test_build_arg_schema_enum() -> None:
    schema = build_arg_schema(with_enum)
    assert schema.args[0].py_type is Difficulty


def test_build_arg_schema_rejects_unsupported_type() -> None:
    with pytest.raises(SchemaBuildError, match="blob"):
        build_arg_schema(unsupported)
