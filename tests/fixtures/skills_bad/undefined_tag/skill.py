from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함4", tags=["no-such-tag"])
async def broken(ctx: ToolContext) -> ToolResult:
    """정의되지 않은 태그."""
    return ToolResult(data={})
