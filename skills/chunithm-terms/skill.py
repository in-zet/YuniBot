# skills/chunithm-terms/skill.py
from __future__ import annotations

from core.decorators import tool
from core.tables import RowRef, TableBuilder, cell
from core.tool_types import Display, ToolContext, ToolResult

_TERM = "chunithm-terms.term"
_T_NAME = "용어"
_T_CATEGORY = "분류"


@tool(slash="용어 뜻")
async def explain(ctx: ToolContext, term: str) -> ToolResult:
    """용어의 뜻, 별칭, 값, 출처를 보여준다."""
    explanation = await ctx.terms.explain(term, ctx)
    if explanation is None:
        return ToolResult(error=f"'{term}'은 모르는 용어다. `/용어 제안`으로 등록을 제안할 수 있다")

    lines = [f"{explanation.canonical} ({explanation.category_label})"]
    if explanation.value is not None:
        lines.append(f"값: {explanation.value:,}")
    if explanation.meaning:
        lines.append(explanation.meaning)
    if explanation.aliases:
        lines.append(f"별칭: {', '.join(explanation.aliases)}")
    lines.append(f"출처: {explanation.source} ({explanation.scope})")

    return ToolResult(
        data={
            "canonical": explanation.canonical, "category": explanation.category_label,
            "value": explanation.value, "scope": explanation.scope,
        },
        display=Display(content="\n".join(lines)),
        display_mode="optional",
    )


@tool(slash="용어 목록")
async def list(ctx: ToolContext, category: str | None = None) -> ToolResult:
    """분류별 용어 목록을 표로 보여준다. 분류를 안 주면 전부 보여준다."""
    try:
        items = await ctx.terms.list_terms(category, ctx)
    except ValueError as exc:
        return ToolResult(error=str(exc))

    data = {"count": len(items), "terms": [item.canonical for item in items]}
    if not items:
        return ToolResult(data=data, display=Display(content="등록된 용어가 없다."))
    builder = TableBuilder(unit="용어")
    builder.column(_T_NAME)
    builder.column(_T_CATEGORY)
    builder.step(f"용어 목록 ({category})" if category else "용어 목록")
    for item in items:
        builder.row(RowRef(_TERM, item.canonical), {_T_NAME: cell(item.canonical), _T_CATEGORY: cell(item.category_label)})
    return ToolResult(data=data, table=builder.build())


_REPORT_CHANNEL_SETTING_SKILL = "admin"
_REPORT_CHANNEL_SETTING_NAME = "보고 채널"


@tool(slash="용어 제안", mutates=True)
async def suggest(ctx: ToolContext, term: str, meaning: str, aliases: str) -> ToolResult:
    """서버 은어를 등록한다. 승인을 기다리지 않고 바로 반영되고, admin의 보고 채널이
    설정돼 있으면 거기로 알린다. 부적절하면 admin이 나중에 끌 수 있다."""
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 용어를 등록할 수 있다")
    alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
    proposal_id = await ctx.terms.propose(
        guild_id=ctx.guild_id, term=term, meaning=meaning, aliases=alias_list, suggested_by=ctx.player_id
    )
    report_channel = await ctx.settings.get_value(
        scope="guild", scope_id=ctx.guild_id, skill=_REPORT_CHANNEL_SETTING_SKILL,
        name=_REPORT_CHANNEL_SETTING_NAME, default="",
    )
    if report_channel:
        alias_text = f" (별칭: {', '.join(alias_list)})" if alias_list else ""
        await ctx.notify.post(
            report_channel,
            f"서버 은어 등록: '{term}' — {meaning}{alias_text}\n등록한 플레이어: {ctx.player_id}\nid: {proposal_id}",
        )
    return ToolResult(
        data={"proposal_id": proposal_id},
        display=Display(content=f"'{term}' 은어를 등록했어. (id: {proposal_id})"),
        display_mode="optional",
    )


@tool(slash="용어 반영여부", tags=["admin"], mutates=True, confirm="always")
async def review(ctx: ToolContext, proposal_id: str, approve: bool) -> ToolResult:
    """등록된 은어를 끄거나(approve=False, 철회) 다시 켠다(approve=True, 되돌리기)."""
    proposal = await ctx.terms.review(proposal_id, approve=approve, reviewed_by=ctx.player_id)
    if proposal is None:
        return ToolResult(error="그런 등록이 없다")
    status_text = "켰다" if approve else "껐다"
    return ToolResult(
        data={"proposal_id": proposal_id, "status": proposal.status},
        display=Display(content=f"'{proposal.term}' 등록을 {status_text}."),
        display_mode="optional",
    )
