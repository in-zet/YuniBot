from __future__ import annotations

from datetime import UTC, datetime

from core.decorators import tool
from core.tool_types import Display, ToolContext, ToolResult
from services.common.websearch import NoSourcesError, SearchCooldownError


def _now() -> datetime:
    return datetime.now(UTC)


@tool(cost="llm", max_calls_per_turn=5)
async def search(ctx: ToolContext, question: str) -> ToolResult:
    """츄니즘 일반 질문(시스템, 공략, 이벤트 등)을 웹에서 찾아 내용과 출처를 돌려준다. 상수·노트
    수·수록 버전이나 용어 사전에 있는 말은 이 도구를 쓰지 말고 chunithm-song/chunithm-terms를 쓴다."""
    try:
        result = await ctx.websearch.search(query=question, player_id=ctx.player_id, now=_now())
    except SearchCooldownError:
        return ToolResult(error="검색은 조금 더 기다렸다가 다시 해 달라고 전해라")
    except NoSourcesError:
        return ToolResult(error="이 질문은 출처를 찾지 못했다. 모른다고 답하고 공식 사이트나 위키를 찾아보라고 제안해라")

    if ctx.usage is not None:
        await ctx.usage.record(
            guild_id=ctx.guild_id or "dm", player_id=ctx.player_id, model=ctx.websearch.model, kind="websearch",
            input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
        )

    sources = [{"title": s.title, "url": s.url} for s in result.sources]
    source_lines = "\n".join(f"- {s['title']}: {s['url']}" for s in sources)
    return ToolResult(
        data={"answer": result.text, "sources": sources},
        display=Display(content=f"출처\n{source_lines}"),
        display_mode="required",
    )
