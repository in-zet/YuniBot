# services/common/sega_cookies.py
"""저장된 `clal`(SEGA 공통 인증 쿠키) 읽기와 로그인 만료 처리. 게임마다 있는 NET 서비스가 함께 쓴다.
`clal`은 게임 공통이라 `sega_cookies` 테이블 하나에 있고, 복호화는 NET 서비스 안에서만 한다(CLAUDE.md)."""

from __future__ import annotations

import aiosqlite

from core.tool_types import ToolContext
from services.common.players import PlayersService
from services.common.secrets import SecretsService


class SegaCookieStore:
    def __init__(self, *, conn: aiosqlite.Connection, secrets: SecretsService, players: PlayersService) -> None:
        self._conn = conn
        self._secrets = secrets
        self._players = players

    async def clal(self, player_id: str) -> str | None:
        """복호화한 `clal`. 평문은 부른 쪽 스코프에만 잠깐 있어야 한다 — 로그나 LLM 입력에 넣지 않는다."""
        row = await (
            await self._conn.execute("SELECT encrypted_cookie FROM sega_cookies WHERE player_id = ?", (player_id,))
        ).fetchone()
        if row is None:
            return None
        return self._secrets.decrypt(row[0])

    async def expire(self, player_id: str, ctx: ToolContext | None) -> None:
        """인증 실패(쿠키 만료) 처리: sega-linked를 떼고 sega.login.expired를 낸다.
        이벤트의 대상은 쿠키 주인(player_id)이다 — 최근 기록 조회(report.recent)처럼 요청한 사람과 쿠키 주인이 다를
        수 있으므로 ctx.emit(요청한 사람 기준) 대신 버스에 대상 플레이어를 직접 넘긴다."""
        await self._players.revoke_status_tag(player_id, "sega-linked")
        if ctx is not None:
            await ctx.event_bus.emit_notify(
                "sega.login.expired", {"player_id": player_id},
                player_id=player_id, guild_id=ctx.guild_id,
            )
