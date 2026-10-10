from __future__ import annotations

from core.decorators import on
from core.tool_types import ToolContext


@on("players.first_seen")
async def handle_first_seen(
    ctx: ToolContext,
) -> NotDefinedAnywhere:  # noqa: F821 # pyright: ignore[reportUndefinedVariable] - 일부러 정의하지 않은 이름(이 픽스처의 목적)
    """반환 타입 힌트(NotDefinedAnywhere)가 어디에도 정의돼 있지 않다.

    core.events.handler_kind()가 get_type_hints()로 이 힌트를 해석하려다 NameError를
    던진다 — core/bot.py의 _register_event_handlers가 이 예외를 잡아 이 스킬 하나만
    비활성화해야 한다(코어 전체가 죽으면 안 된다).
    """
