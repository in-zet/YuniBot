from __future__ import annotations

from core.decorators import tool
from core.skill_loading import SkillLoadState
from core.tool_types import Display, ToolContext, ToolResult


def _load_state(ctx: ToolContext) -> SkillLoadState | None:
    # core/agent.py가 매 턴 ctx.services["_skill_load_state"]에 이번 요청의 SkillLoadState를
    # 넣어 준다. 코어 도구 전용 내부 키라 manifest.services 선언 대상이 아니다
    # (스킬이 아니라 core/agent.py 자신이 만드는 ToolContext이기 때문).
    return ctx.services.get("_skill_load_state")  # pyright: ignore[reportReturnType]


@tool()
async def open_skill(ctx: ToolContext, skill_name: str) -> ToolResult:
    """스킬 하나를 펼친다. 이후 호출부터 그 스킬의 본문 지침과 도구 정의를 쓸 수 있다.

    여러 스킬을 한 번에 펼치려면 같은 턴에 이 도구를 스킬마다 한 번씩 부른다."""
    state = _load_state(ctx)
    if state is None:
        return ToolResult(error="스킬을 펼칠 수 없는 상태다")
    if skill_name not in state._known_skills:
        return ToolResult(error=f"'{skill_name}'이라는 스킬이 없다")
    state.open(skill_name)
    return ToolResult(data={"opened": skill_name})


@tool()
async def remember(ctx: ToolContext, content: str | None = None, nickname: str | None = None) -> ToolResult:
    """플레이어가 "이거 기억해 줘"라고 직접 요청한 내용을 플레이어 메모리에 바로 저장한다. 한 줄에 사실
    하나로 짧게 쓴다. 플레이어가 자기를 부를 이름을 정해 주면 content가 아니라 nickname에 그 이름만 넣는다."""
    if not (content and content.strip()) and not (nickname and nickname.strip()):
        return ToolResult(error="기억할 내용이나 호칭이 필요하다")
    saved = await ctx.memory.remember(ctx.player_id, content=content, nickname=nickname)
    if saved.content_saved is None and saved.nickname_saved is None:
        return ToolResult(error="저장할 수 있는 내용이 없다")
    return ToolResult(
        data={"saved": saved.content_saved, "nickname": saved.nickname_saved},
        display=Display(content="기억했다.", ephemeral=True),
    )


@tool()
async def list_capabilities(ctx: ToolContext, category: str | None = None) -> ToolResult:
    """등록된 스킬을 바탕으로 할 수 있는 일을 분류별로 안내한다. category를 주면 그 분류만 보여준다."""
    # 실제 카드 목록 조립은 core/capabilities.py가 맡는다 — 여기서는 그 모듈을 부르기만 한다.
    from core.capabilities import (  # pyright: ignore[reportMissingImports]
        build_capabilities_result,
    )

    return await build_capabilities_result(ctx, category=category)


CORE_TOOL_SPECS = [open_skill.__yuni_tool__, remember.__yuni_tool__, list_capabilities.__yuni_tool__]  # pyright: ignore[reportFunctionMemberAccess]
