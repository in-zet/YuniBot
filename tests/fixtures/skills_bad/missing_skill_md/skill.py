# SKILL.md가 아예 없다.
from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함1")
async def broken(ctx: ToolContext) -> ToolResult:
    """SKILL.md가 없는 스킬."""
    return ToolResult(data={})
