# skills/memory/skill.py
from __future__ import annotations

from core.decorators import tool
from core.tool_types import Display, ToolContext, ToolResult
from services.common.memory import NICKNAME_KEYWORD, MemoryState

_EMPTY_TEXT = "아직 기억하고 있는 게 없어."


def _lines(state: MemoryState) -> list[str]:
    return [line.strip() for line in state.content.splitlines() if line.strip()]


def _render(state: MemoryState) -> str:
    lines = _lines(state)
    parts: list[str] = []
    if state.nickname:
        parts.append(f"{NICKNAME_KEYWORD}: {state.nickname}")
    parts.extend(f"- {line}" for line in lines)
    body = "\n".join(parts) if parts else _EMPTY_TEXT
    return f"{body}\n\n자동 저장: {'켜짐' if state.auto_save else '꺼짐'}"


@tool(slash="메모리 보기")
async def view(ctx: ToolContext) -> ToolResult:
    """유니가 이 플레이어에 대해 기억하는 내용(호칭 포함) 전문을 본인에게만 보낸다. 채널에는 보이지 않는다."""
    state = await ctx.memory.get_state(ctx.player_id)
    return ToolResult(
        data={
            "lines": len(_lines(state)), "has_nickname": bool(state.nickname), "auto_save": state.auto_save,
            "delivery": "본인에게만 보냈다(내용은 채널에 말하지 않는다)",
        },
        display=Display(content=_render(state), ephemeral=True, private=True),
    )


@tool(slash="메모리 지우기", mutates=True, confirm="bulk")
async def forget(ctx: ToolContext, what: str | None = None, *, dry_run: bool = False) -> ToolResult:
    """기억한 내용에서 what에 맞는 줄을 지운다. what은 지울 줄의 내용이고, 호칭은 "호칭"이라고 준다. what을 비우면
    기억한 내용과 호칭을 전부 지운다. 2개 이상 지울 때는 실행기가 지울 개수를 보여 주고 확인을 받은 뒤에 지운다.
    지운 항목은 다시 기억하지 않는다. 본인 메모리만 지운다."""
    found = await ctx.memory.find_forget(ctx.player_id, what)
    if not found:
        state = await ctx.memory.get_state(ctx.player_id)
        current = _lines(state)
        if state.nickname:
            current.append(f"{NICKNAME_KEYWORD}: {state.nickname}")
        if not current:
            return ToolResult(error="지울 내용이 없다. 기억하고 있는 게 없다.")
        listing = " / ".join(current)
        return ToolResult(error=f"'{what}'에 맞는 항목이 없다. 지금 기억한 줄: {listing}")
    shown = "\n".join(f"- {item}" for item in found)
    if dry_run:
        return ToolResult(
            data={"count": len(found), "items": found},
            display=Display(content=f"이 {len(found)}개를 지울게:\n{shown}", ephemeral=True, private=True),
            display_mode="optional",
        )
    removed = await ctx.memory.forget(ctx.player_id, what)
    return ToolResult(
        data={"deleted_count": len(removed), "items": removed},
        display=Display(content="지웠어:\n" + "\n".join(f"- {item}" for item in removed), ephemeral=True, private=True),
        display_mode="optional",
    )


@tool(slash="메모리 자동저장", mutates=True)
async def set_auto(ctx: ToolContext, on: bool) -> ToolResult:
    """대화에서 자동으로 기억하는 것을 켜거나(on=True) 끈다. 꺼도 이미 기억한 내용은 남고 계속 쓰인다."""
    await ctx.memory.set_auto_save(ctx.player_id, on)
    text = "이제부터 대화에서 알게 된 걸 기억할게." if on else "이제부터 대화에서 알게 된 건 기억하지 않을게. 이미 기억한 건 그대로 남아 있어."
    return ToolResult(data={"auto_save": on}, display=Display(content=text), display_mode="optional")
