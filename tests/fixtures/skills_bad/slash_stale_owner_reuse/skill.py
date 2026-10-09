from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="stale-clean")
async def reuse(ctx: ToolContext) -> ToolResult:
    """비활성화된 slash_stale_owner_conflict가 쓰던 경로를 재사용한다."""
    return ToolResult(data={})
