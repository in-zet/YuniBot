# skills/admin/skill.py
from __future__ import annotations

from core.decorators import tool
from core.tool_types import PlayerRef, ToolContext, ToolResult
from services.common.players import Player


@tool(slash="관리 사용량")
async def usage(ctx: ToolContext) -> ToolResult:
    """기간별 LLM 호출 수, 토큰, 추정 비용을 보여준다. LLM이 아직 없어 자리만 채운다."""
    return ToolResult(data={"calls": 0, "tokens": 0, "estimated_cost_usd": 0.0})


@tool(slash="관리 스킬")
async def skills(ctx: ToolContext) -> ToolResult:
    """등록된 스킬, 켜짐 여부, 등록 실패한 스킬과 이유를 보여준다."""
    snapshot = ctx.registry_snapshot
    if snapshot is None:
        return ToolResult(error="스킬 현황을 아직 알 수 없다")
    lines = [f"- {s.name} ({s.game}, v{s.version})" for s in snapshot.skills]
    lines += [f"- {name} 등록 실패: {reason}" for name, reason in snapshot.failures.items()]
    return ToolResult(
        data={"enabled": [s.name for s in snapshot.skills], "failures": snapshot.failures},
        display_mode="optional",
    )


@tool(slash="관리 스킬설정", mutates=True, confirm="always")
async def toggle_skill(ctx: ToolContext, skill_name: str, enabled: bool) -> ToolResult:
    """이 서버에서 스킬을 켜거나 끈다."""
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 쓸 수 있다")
    await ctx.settings.set_skill_enabled(
        guild_id=ctx.guild_id, skill=skill_name, enabled=enabled, updated_by=ctx.player_id
    )
    return ToolResult(data={"skill": skill_name, "enabled": enabled})


@tool(slash="관리 태그부여", mutates=True, confirm="always")
async def grant_tag(ctx: ToolContext, target: PlayerRef, tag: str) -> ToolResult:
    """부여 태그·스킬 태그를 붙인다. 상태 태그는 거부한다."""
    assert isinstance(target, Player)
    try:
        await ctx.players.grant_tag(target.id, tag, source=f"admin:{ctx.player_id}")
    except ValueError as exc:
        return ToolResult(error=str(exc))
    return ToolResult(data={"player_id": target.id, "granted": tag})


@tool(slash="관리 태그회수", mutates=True, confirm="always")
async def revoke_tag(ctx: ToolContext, target: PlayerRef, tag: str) -> ToolResult:
    """부여 태그·스킬 태그를 뗀다. 상태 태그는 거부하고, 마지막 admin은 못 뗀다."""
    assert isinstance(target, Player)
    if tag == "admin":
        holders = await ctx.players.players_with_tag("admin")
        if target.id in holders and len(holders) <= 1:
            return ToolResult(error="마지막 관리자는 뗄 수 없다")
    try:
        await ctx.players.revoke_tag(target.id, tag)
    except ValueError as exc:
        return ToolResult(error=str(exc))
    return ToolResult(data={"player_id": target.id, "revoked": tag})


@tool(slash="관리 상한", mutates=True, confirm="always")
async def set_limits(ctx: ToolContext, monthly_cost_cap: float, player_cooldown_seconds: int) -> ToolResult:
    """월 비용 상한과 플레이어별 쿨다운을 저장한다. 적용(한도 집행)은 6단계다."""
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 쓸 수 있다")
    await ctx.settings.set_value(
        scope="guild", scope_id=ctx.guild_id, skill="admin", name="월 비용 상한",
        value=monthly_cost_cap, updated_by=ctx.player_id,
    )
    await ctx.settings.set_value(
        scope="guild", scope_id=ctx.guild_id, skill="admin", name="플레이어 쿨다운",
        value=player_cooldown_seconds, updated_by=ctx.player_id,
    )
    return ToolResult(data={"monthly_cost_cap": monthly_cost_cap, "player_cooldown_seconds": player_cooldown_seconds})
