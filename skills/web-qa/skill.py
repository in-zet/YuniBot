from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from core.decorators import tool
from core.search import SearchSource
from core.tool_types import Display, ToolContext, ToolResult
from services.common.websearch import NoSourcesError, SearchCooldownError

logger = logging.getLogger(__name__)

# 검색 한 번에 기다리는 총 시간. 재시도까지 합쳐 이 시간을 넘기면 포기하고 모델에게 실패를 알린다.
_SEARCH_TOTAL_SECONDS = 60
# 보여 주는 출처는 최대 이만큼, 제목은 이 글자 수까지(2026-10-22 사용자 지시: 출처가 너무 길었다).
_MAX_SOURCES = 3
_MAX_TITLE_CHARS = 40


def _now() -> datetime:
    return datetime.now(UTC)


def _shown_sources(found: list[SearchSource]) -> list[dict[str, str]]:
    """같은 주소는 한 번, 앞에서부터 최대 `_MAX_SOURCES`개, 제목은 짧게."""
    shown: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in found:
        if source.url in seen:
            continue
        seen.add(source.url)
        title = source.title.strip() or source.url
        if len(title) > _MAX_TITLE_CHARS:
            title = title[: _MAX_TITLE_CHARS - 1] + "…"
        shown.append({"title": title, "url": source.url})
        if len(shown) == _MAX_SOURCES:
            break
    return shown


@tool(cost="llm", max_calls_per_turn=5)
async def search(ctx: ToolContext, question: str) -> ToolResult:
    """츄니즘 일반 질문(시스템, 공략, 이벤트 등)을 웹에서 찾아 내용과 출처를 돌려준다. 상수·노트
    수·수록 버전이나 용어 사전에 있는 말은 이 도구를 쓰지 말고 chunithm-song/chunithm-terms를 쓴다."""
    try:
        async with asyncio.timeout(_SEARCH_TOTAL_SECONDS):
            result = await ctx.websearch.search(query=question, player_id=ctx.player_id, now=_now())
    except SearchCooldownError:
        return ToolResult(error="검색은 조금 더 기다렸다가 다시 해 달라고 전해라")
    except NoSourcesError:
        return ToolResult(error="이 질문은 출처를 찾지 못했다. 모른다고 답하고 공식 사이트나 위키를 찾아보라고 제안해라")
    except Exception:
        logger.warning("웹 검색 실패", exc_info=True)
        return ToolResult(error="지금 웹 검색이 안 된다. 같은 검색을 다시 하지 말고, 검색이 안 돼서 못 찾았다고 말하고 잠시 뒤에 다시 물어봐 달라고 전해라")

    if ctx.usage is not None:
        await ctx.usage.record(
            guild_id=ctx.guild_id or "dm", player_id=ctx.player_id, model=ctx.websearch.model, kind="websearch",
            input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
            search_requests=result.usage.search_requests,
        )

    sources = _shown_sources(result.sources)
    source_lines = "\n".join(f"- {s['title']}: {s['url']}" for s in sources)
    return ToolResult(
        data={"answer": result.text, "sources": sources},
        display=Display(content=f"출처\n{source_lines}"),
        display_mode="required",
    )
