# services/common/sega_cookies.py
"""`clal`(SEGA 공통 인증 쿠키) 저장·읽기와 로그인 만료 처리. 게임마다 있는 NET 서비스가 함께 쓴다.
`clal`은 게임 공통이라 `sega_cookies` 테이블 하나에 있고, 복호화는 NET 서비스 안에서만 한다(CLAUDE.md)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from core.tool_types import ToolContext
from services.common.players import PlayersService
from services.common.secrets import SecretsService


@dataclass(slots=True, kw_only=True)
class LoginStatus:
    linked: bool
    nickname: str | None = None
    last_checked_at: str | None = None


class SegaCookieStore:
    def __init__(self, *, conn: aiosqlite.Connection, secrets: SecretsService, players: PlayersService) -> None:
        self._conn = conn
        self._secrets = secrets
        self._players = players

    async def store(self, player_id: str, clal: str, nickname: str) -> None:
        """로그인으로 받은 `clal`을 암호화해 넣고 sega-linked 태그를 붙인다. 어느 게임 NET으로 로그인했든 같은 자리다."""
        encrypted = self._secrets.encrypt(clal)
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO sega_cookies (player_id, encrypted_cookie, linked_at, last_checked_at, nickname) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (player_id) DO UPDATE SET "
            "encrypted_cookie = excluded.encrypted_cookie, linked_at = excluded.linked_at, "
            "last_checked_at = excluded.last_checked_at, nickname = excluded.nickname",
            (player_id, encrypted, now, now, nickname),
        )
        await self._conn.commit()
        await self._players.set_status_tag(player_id, "sega-linked", source="sega_cookies:login")

    async def status(self, player_id: str) -> LoginStatus:
        """로그인 여부와 저장해 둔 닉네임(DB만 읽는다). 쿠키가 있어도 sega-linked가 떼였으면(만료) 안 된 것이다."""
        row = await (
            await self._conn.execute(
                "SELECT last_checked_at, nickname FROM sega_cookies WHERE player_id = ?", (player_id,)
            )
        ).fetchone()
        if row is None or "sega-linked" not in await self._players.tags_for(player_id):
            return LoginStatus(linked=False)
        return LoginStatus(linked=True, nickname=row["nickname"], last_checked_at=row["last_checked_at"])

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
