from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함3")
async def broken(ctx: ToolContext) -> ToolResult:
    """services에 songdb를 선언하지 않았는데 ctx.songdb를 쓴다."""
    ctx.songdb.lookup("크로노")
    return ToolResult(data={})
