from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함5")
async def broken(ctx: ToolContext) -> ToolResult:
    """테이블 접두사 위반 데모."""
    return ToolResult(data={})
