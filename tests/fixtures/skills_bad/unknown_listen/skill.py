from core.decorators import on
from core.tool_types import ToolContext


@on("no.such.event")
async def handle(ctx: ToolContext, payload: dict[str, object]) -> None:
    """아무도 내지 않는 이벤트."""
