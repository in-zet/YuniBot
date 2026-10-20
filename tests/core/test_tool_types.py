# tests/core/test_tool_types.py
import pytest

from core.events import EventBus, Section
from core.tool_types import (
    STANDARD_REF_TYPES,
    ChartRef,
    Display,
    TableRef,
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
    assert result.table is None


def test_display_mode_optional() -> None:
    result = ToolResult(data={}, display=Display(content="hi"), display_mode="optional")
    assert result.display_mode == "optional"


def test_standard_ref_types_registered() -> None:
    assert ChartRef in STANDARD_REF_TYPES
    assert TableRef in STANDARD_REF_TYPES
    assert len(STANDARD_REF_TYPES) == 9


async def test_tool_context_emit_uses_event_bus() -> None:
    bus = EventBus()
    received: list[object] = []

    async def handler(ctx: ToolContext, payload: object) -> None:
        received.append(payload)

    bus.register_notify("players.linked", owner_skill="account-link", fn=handler, skill_services=[])
    ctx = ToolContext(player_id="1", tags=frozenset(), services={}, event_bus=bus, guild_id="g1")

    await ctx.emit("players.linked", {"player_id": "1"})
    await bus.drain()

    assert received == [{"player_id": "1"}]


async def test_tool_context_emit_gather_returns_sections() -> None:
    bus = EventBus()

    async def handler(ctx: ToolContext, payload: object) -> Section | None:
        return Section(data={"ok": True})

    bus.register_gather("chunithm.song.viewed", owner_skill="song", fn=handler, skill_services=[])
    ctx = ToolContext(player_id="1", tags=frozenset(), services={}, event_bus=bus)

    sections = await ctx.emit_gather("chunithm.song.viewed", {})

    assert sections == [Section(data={"ok": True})]


def test_tool_context_defaults_guild_id_and_event_bus() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={})
    assert ctx.guild_id is None
    assert ctx.event_bus is not None


def test_tool_context_db_defaults_to_none() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={})
    assert ctx.db is None


def test_tool_context_discord_id_defaults_to_none() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={})
    assert ctx.discord_id is None


def test_tool_context_discord_id_round_trips() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={}, discord_id="111")
    assert ctx.discord_id == "111"


def test_display_ephemeral_defaults_to_false() -> None:
    assert Display(content="hi").ephemeral is False
