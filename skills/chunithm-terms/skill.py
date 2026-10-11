# skills/chunithm-terms/skill.py
from __future__ import annotations

from core.decorators import tool
from core.tool_types import Display, ToolContext, ToolResult


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
    """분류별 용어 목록을 보여준다. 분류를 안 주면 전부 보여준다."""
    try:
        items = await ctx.terms.list_terms(category, ctx)
    except ValueError as exc:
        return ToolResult(error=str(exc))

    lines = [f"{item.canonical} ({item.category_label})" for item in items]
    return ToolResult(
        data={"count": len(items), "terms": [item.canonical for item in items]},
        display=Display(content="\n".join(lines) if lines else "등록된 용어가 없다."),
    )


@tool(slash="용어 제안")
async def suggest(ctx: ToolContext, term: str, meaning: str, aliases: str) -> ToolResult:
    """서버 은어를 제안한다. admin이 승인해야 반영된다."""
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 용어를 제안할 수 있다")
    alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
    proposal_id = await ctx.terms.propose(
        guild_id=ctx.guild_id, term=term, meaning=meaning, aliases=alias_list, suggested_by=ctx.player_id
    )
    return ToolResult(
        data={"proposal_id": proposal_id},
        display=Display(content=f"'{term}' 제안을 등록했다. admin 승인을 기다린다. (제안 id: {proposal_id})"),
        display_mode="optional",
    )


@tool(slash="용어 승인", tags=["admin"], mutates=True, confirm="always")
async def review(ctx: ToolContext, proposal_id: str, approve: bool) -> ToolResult:
    """제안을 승인하거나 거절한다."""
    proposal = await ctx.terms.review(proposal_id, approve=approve, reviewed_by=ctx.player_id)
    if proposal is None:
        return ToolResult(error="그런 제안이 없거나 이미 처리됐다")
    status_text = "승인" if approve else "거절"
    return ToolResult(
        data={"proposal_id": proposal_id, "status": proposal.status},
        display=Display(content=f"'{proposal.term}' 제안을 {status_text}했다."),
        display_mode="optional",
    )
