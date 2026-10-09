from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="템플릿")
async def example(ctx: ToolContext, name: str) -> ToolResult:
    """예시 도구. 복사한 뒤 지우거나 바꾼다."""
    return ToolResult(data={"name": name})
