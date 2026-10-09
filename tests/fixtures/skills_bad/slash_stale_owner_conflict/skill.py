from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="stale-clean")
async def aaa_clean(ctx: ToolContext) -> ToolResult:
    """나중에 다른 스킬이 재사용할 깨끗한 경로. 함수 이름이 알파벳 순으로 먼저 처리된다."""
    return ToolResult(data={})


@tool(slash="stale-group")
async def zzz_conflict(ctx: ToolContext) -> ToolResult:
    """stale-group을 단독 명령으로 써서 slash_stale_owner_group의 그룹 등록과 충돌한다."""
    return ToolResult(data={})
