from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함6")
async def broken(ctx: ToolContext) -> ToolResult:
    """마이그레이션 파일 이름 위반 데모."""
    return ToolResult(data={})
