from core.decorators import slash_only, tool
from core.tool_types import ToolContext, ToolResult


@slash_only("연습 체험시작")
async def start_demo(ctx: ToolContext) -> ToolResult:
    """연습 체험을 시작해 practice-unlocked 태그를 받는다.

    2단계 데모 전용: data의 `_demo_grant_tag` 표시를 보고 슬래시 경로(core/slash.py)가 요청한
    플레이어에게 이 스킬이 정의한 태그를 임시 태그 제공자로 부여한다. 3단계 players 서비스가
    생기면 실제 부여 방식으로 바뀐다.
    """
    return ToolResult(data={"_demo_grant_tag": "practice-unlocked"})


@tool(slash="연습 보기", tags=["practice-unlocked"])
async def view_demo(ctx: ToolContext) -> ToolResult:
    """체험을 시작한 플레이어만 볼 수 있는 연습용 도구."""
    return ToolResult(data={"message": "연습 콘텐츠를 볼 수 있다"})


@tool(slash="연습 비밀", tags=["practice-secret"])
async def secret_demo(ctx: ToolContext) -> ToolResult:
    """완전히 숨겨지는 부여 태그 데모."""
    return ToolResult(data={"message": "숨겨진 콘텐츠"})


@tool(slash="연습 초기화", mutates=True, confirm="always")
async def reset_demo(ctx: ToolContext) -> ToolResult:
    """확인 버튼 플로우를 보여주는 데모. 실제로 바꾸는 상태는 없다."""
    return ToolResult(data={"reset": True})
