from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함7")
async def emit_something(ctx: ToolContext) -> ToolResult:
    """some.event를 낸다고 manifest.emits에 선언만 한다(실제 emit 호출은 하지 않음)."""
    return ToolResult(data={})
