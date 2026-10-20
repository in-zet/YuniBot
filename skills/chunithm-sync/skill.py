# skills/chunithm-sync/skill.py
from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError

from core.decorators import tool
from core.events import Section
from core.tool_types import ArcadeRef, ChartRef, Display, ToolContext, ToolResult
from services.chunithm.chart_ref import ResolvedChart
from services.common.arcades import Arcade

logger = logging.getLogger(__name__)

_SKILL_NAME = "chunithm-sync"
_COOLDOWN_SETTING = "동기화 쿨다운"
_DEFAULT_COOLDOWN_SECONDS = 120
_SECTION_SEPARATOR = "\n\n"


def _now() -> datetime:
    """테스트가 바꿔 끼우는 현재 시각(쿨다운 계산용)."""
    return datetime.now(UTC)


def _rating_text(value: Decimal | None) -> str:
    return "계산 불가" if value is None else str(value)


async def _cooldown_remaining(ctx: ToolContext, last_sync_at: str | None) -> int:
    """다음 동기화까지 남은 초(올림). 0이면 지금 해도 된다. 서버 밖(DM)에서는 서버
    설정을 읽을 곳이 없으므로 기본값을 쓴다."""
    if last_sync_at is None:
        return 0
    cooldown: int = _DEFAULT_COOLDOWN_SECONDS
    if ctx.guild_id is not None:
        cooldown = int(
            await ctx.settings.get_value(
                scope="guild", scope_id=ctx.guild_id, skill=_SKILL_NAME,
                name=_COOLDOWN_SETTING, default=_DEFAULT_COOLDOWN_SECONDS,
            )
        )
    elapsed = (_now() - datetime.fromisoformat(last_sync_at)).total_seconds()
    return max(0, math.ceil(cooldown - elapsed))


def _render(yuni_line: str, sections: list[Section], footer: str | None) -> str:
    """유니 한마디(맨 위) + 표시물이 있는 조각의 content(이미 order 순) + 꼬리말을 잇는다.
    display가 None인 조각(귀띔만 주는 조각)은 표시하지 않는다."""
    parts: list[str] = [yuni_line] if yuni_line else []
    for section in sections:
        display = section.display
        if isinstance(display, Display) and display.content:
            parts.append(display.content)
    if footer:
        parts.append(footer)
    return _SECTION_SEPARATOR.join(parts)


@tool(slash="동기화", tags=["chunithm-linked"], cost="network")
async def run(
    ctx: ToolContext, focus: ChartRef | None = None, quiet: bool = False, arcade: ArcadeRef | None = None
) -> ToolResult:
    """CHUNITHM-NET에서 최신 기록을 가져와 저장하고, 지난 동기화와의 차이를 리포트로 낸다. focus로 볼 채보 하나, arcade로 친 오락실을 넘길 수 있다."""
    assert focus is None or isinstance(focus, ResolvedChart)
    assert arcade is None or isinstance(arcade, Arcade)

    # save_sync가 이번 시각으로 덮어쓰기 전에 "직전" 동기화 시각을 읽어 둔다.
    previous_synced_at: str | None = await ctx.records.last_sync_at(ctx.player_id)
    remaining = await _cooldown_remaining(ctx, previous_synced_at)
    if remaining > 0:
        return ToolResult(
            error=f"방금 동기화했어. {remaining}초 뒤에 다시 해 줘.",
            data={"cooldown_remaining_seconds": remaining},
        )

    try:
        fetch = await ctx.chunithm_net.fetch_for_sync(ctx.player_id, ctx=ctx)
    except MaintenanceError:
        return ToolResult(error="CHUNITHM-NET이 점검 중이야. 점검이 끝나면 다시 동기화해 줘.")
    except (AuthenticationError, ConnectionError):
        return ToolResult(error="로그인이 풀렸어. /로그인으로 다시 연결해 줘.")

    result = await ctx.records.save_sync(ctx.player_id, fetch, ctx)
    # 저장이 일어났으니(기준점만 만든 첫 동기화도) 이 플레이어의 점수가 담긴 작업 표는 옛 값이다.
    if ctx.tables is not None:
        ctx.tables.mark_stale(ctx.player_id)
    rating_data: dict[str, Any] = {
        "computed_rating": _rating_text(result.computed_rating),
        "displayed_rating": None if result.displayed_rating is None else str(result.displayed_rating),
        "rating_mismatch": result.rating_mismatch,
    }

    if result.is_first_sync:
        content = f"기준점을 잡았어. 다음 동기화부터 바뀐 점을 알려 줄게.\n현재 레이팅: {rating_data['computed_rating']}"
        return ToolResult(
            data={"sync_id": result.sync_id, "is_first_sync": True, **rating_data},
            display=Display(content=content),
        )

    diff = await ctx.records.sync_diff(result.sync_id)
    completed: dict[str, Any] = {
        "player_id": ctx.player_id,
        "sync_id": result.sync_id,
        "sessions": [
            {"id": s.id, "play_date": s.play_date, "estimated": s.estimated}
            for s in (diff.sessions if diff is not None else [])
        ],
        "has_new_plays": result.has_new_plays,
        "is_first_sync": False,
        # 지금은 채보 하나만 받지만, 여러 개를 받게 될 자리를 위해 리스트 모양을 유지한다.
        "focus": (
            [{"song_title": focus.song.title, "difficulty": focus.chart.difficulty}] if focus is not None else None
        ),
        "previous_synced_at": previous_synced_at,
        "arcade_id": arcade.id if arcade is not None else None,
    }

    # 변화가 없어도 모은다 — 받는 쪽이 has_new_plays를 보고 현재값만 돌려준다.
    sections = await ctx.emit_gather("chunithm.sync.completed", completed)
    yuni_line = "" if quiet else await ctx.yuni.line(sections, player_id=ctx.player_id, guild_id=ctx.guild_id)

    footer: str | None = None
    if result.rating_mismatch:
        logger.warning(
            "rating mismatch for player %s: computed=%s displayed=%s (sync %s)",
            ctx.player_id, rating_data["computed_rating"], rating_data["displayed_rating"], result.sync_id,
        )
        footer = (
            f"※ 계산한 레이팅({rating_data['computed_rating']})이 "
            f"CHUNITHM-NET 표시값({rating_data['displayed_rating']})과 어긋나."
        )
    rendered_text = _render(yuni_line, sections, footer) or "동기화를 마쳤어."

    player = await ctx.players.get_player(ctx.player_id)
    if player is None or player.sync_report_public:
        is_public = True
        # ToolContext는 지금 채널을 모른다(chunithm-sync.md "공개 범위") — 채널 id는 비워 둔다.
        sent_location: dict[str, Any] = {"kind": "channel", "channel_id": None}
        display = Display(content=rendered_text)
    else:
        is_public = False
        await ctx.notify.dm(ctx.player_id, rendered_text)
        # notify.dm은 대표 계정으로 보낸다 — 부른 계정(ctx.discord_id)이 아니라 실제 받은 계정을 적는다.
        sent_location = {"kind": "dm", "discord_id": player.primary_discord_id}
        display = Display(content="동기화 리포트를 DM으로 보냈어.", ephemeral=True)

    sections_data = [section.data for section in sections]
    await ctx.emit(
        "chunithm.sync.reported",
        {
            **completed,
            "rendered_text": rendered_text,
            "sections_data": sections_data,
            "yuni_line": yuni_line,
            "sent_location": sent_location,
        },
    )

    return ToolResult(
        data={
            "sync_id": result.sync_id,
            "is_first_sync": False,
            "has_new_plays": result.has_new_plays,
            "session_dates": [s["play_date"] for s in completed["sessions"]],
            "report_public": is_public,
            "sections_data": sections_data,
            **rating_data,
        },
        display=display,
    )
