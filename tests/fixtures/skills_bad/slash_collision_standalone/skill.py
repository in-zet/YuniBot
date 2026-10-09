from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="연습")
async def standalone(ctx: ToolContext) -> ToolResult:
    """"연습"을 단독 명령으로 등록한다."""
    return ToolResult(data={})
