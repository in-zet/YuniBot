# tests/core/test_tool_types.py
import pytest

from core.events import EventBus
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


async def test_tool_context_emit_uses_event_bus() -> None:
    bus = EventBus()
    received: list[object] = []

    async def handler(payload: object) -> None:
        received.append(payload)

    bus.register_notify("players.linked", owner_skill="account-link", fn=handler)
    ctx = ToolContext(player_id="1", tags=frozenset(), services={}, event_bus=bus)

    await ctx.emit("players.linked", {"player_id": "1"})
    await bus.drain()

    assert received == [{"player_id": "1"}]


def test_tool_context_defaults_guild_id_and_event_bus() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={})
    assert ctx.guild_id is None
    assert ctx.event_bus is not None


def test_tool_context_db_defaults_to_none() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={})
    assert ctx.db is None
