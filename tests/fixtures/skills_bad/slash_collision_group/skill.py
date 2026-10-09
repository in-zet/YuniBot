from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="연습 충돌")
async def sub(ctx: ToolContext) -> ToolResult:
    """"연습"을 그룹으로 요구하는 하위 명령. slash_collision_standalone과 충돌한다."""
    return ToolResult(data={})
