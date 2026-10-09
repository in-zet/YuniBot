from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="stale-group 하위")
async def sub(ctx: ToolContext) -> ToolResult:
    """stale-group을 그룹 루트로 만드는 하위 명령."""
    return ToolResult(data={})
