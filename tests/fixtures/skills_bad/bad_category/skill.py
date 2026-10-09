from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함2")
async def broken(ctx: ToolContext) -> ToolResult:
    """잘못된 category."""
    return ToolResult(data={})
