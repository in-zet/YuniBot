from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함6")
async def broken(ctx: ToolContext) -> ToolResult:
    """listens에 선언한 이벤트를 처리하는 핸들러가 없다."""
    return ToolResult(data={})
