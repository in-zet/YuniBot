# tests/core/test_tool_types.py
import pytest

from core.tool_types import (
    STANDARD_REF_TYPES,
    ChartRef,
    Display,
    ToolContext,
    ToolResult,
    UndeclaredServiceError,
)


class _FakeSongdb:
    def ping(self) -> str:
        return "pong"


def test_tool_context_exposes_declared_service() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={"songdb": _FakeSongdb()})
    assert ctx.songdb.ping() == "pong"


def test_tool_context_rejects_undeclared_service() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={"songdb": _FakeSongdb()})
    with pytest.raises(UndeclaredServiceError, match="records"):
        _ = ctx.records


def test_tool_result_defaults() -> None:
    result = ToolResult(data={"borders": 1})
    assert result.display is None
    assert result.display_mode == "required"
    assert result.error is None


def test_display_mode_optional() -> None:
    result = ToolResult(data={}, display=Display(content="hi"), display_mode="optional")
    assert result.display_mode == "optional"


def test_standard_ref_types_registered() -> None:
    assert ChartRef in STANDARD_REF_TYPES
    assert len(STANDARD_REF_TYPES) == 8
