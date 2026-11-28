# services/common/account_cookies.py
"""게임 계정 로그인 쿠키 저장·읽기와 로그인 만료 처리의 공통 틀. 계정마다 테이블·상태 태그·만료 이벤트가 다르다:
SEGA(`sega_cookies`, `sega-linked`, `sega.login.expired` — CHUNITHM·maimai 공통)와 Arcaea(`arcaea_sessions`,
`arcaea-linked`, `arcaea.login.expired`). 복호화는 그 계정의 NET 서비스 안에서만 한다(CLAUDE.md)."""

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


@dataclass(slots=True, kw_only=True, frozen=True)
class AccountKind:
    """계정 하나의 저장 자리. 테이블 이름은 코드 상수만 쓴다(SQL에 그대로 들어간다)."""

    table: str
    tag: str
    expired_event: str


SEGA_ACCOUNT = AccountKind(table="sega_cookies", tag="sega-linked", expired_event="sega.login.expired")
ARCAEA_ACCOUNT = AccountKind(table="arcaea_sessions", tag="arcaea-linked", expired_event="arcaea.login.expired")


class AccountCookieStore:
    def __init__(
        self, *, conn: aiosqlite.Connection, secrets: SecretsService, players: PlayersService, kind: AccountKind
    ) -> None:
        self._conn = conn
        self._secrets = secrets
        self._players = players
        self._kind = kind

    async def store(self, player_id: str, cookie: str, nickname: str) -> None:
        """로그인으로 받은 쿠키를 암호화해 넣고 그 계정의 상태 태그를 붙인다."""
        encrypted = self._secrets.encrypt(cookie)
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            f"INSERT INTO {self._kind.table} (player_id, encrypted_cookie, linked_at, last_checked_at, nickname) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (player_id) DO UPDATE SET "
            "encrypted_cookie = excluded.encrypted_cookie, linked_at = excluded.linked_at, "
            "last_checked_at = excluded.last_checked_at, nickname = excluded.nickname",
            (player_id, encrypted, now, now, nickname),
        )
        await self._conn.commit()
        await self._players.set_status_tag(player_id, self._kind.tag, source=f"{self._kind.table}:login")

    async def status(self, player_id: str) -> LoginStatus:
        """로그인 여부와 저장해 둔 닉네임(DB만 읽는다). 쿠키가 있어도 상태 태그가 떼였으면(만료) 안 된 것이다."""
        row = await (
            await self._conn.execute(
                f"SELECT last_checked_at, nickname FROM {self._kind.table} WHERE player_id = ?", (player_id,)
            )
        ).fetchone()
        if row is None or self._kind.tag not in await self._players.tags_for(player_id):
            return LoginStatus(linked=False)
        return LoginStatus(linked=True, nickname=row["nickname"], last_checked_at=row["last_checked_at"])

    async def cookie(self, player_id: str) -> str | None:
        """복호화한 쿠키. 평문은 부른 쪽 스코프에만 잠깐 있어야 한다 — 로그나 LLM 입력에 넣지 않는다."""
        row = await (
            await self._conn.execute(f"SELECT encrypted_cookie FROM {self._kind.table} WHERE player_id = ?", (player_id,))
        ).fetchone()
        if row is None:
            return None
        return self._secrets.decrypt(row[0])

    async def delete(self, player_id: str) -> None:
        """로그아웃: 쿠키를 지우고 상태 태그를 뗀다."""
        await self._conn.execute(f"DELETE FROM {self._kind.table} WHERE player_id = ?", (player_id,))
        await self._conn.commit()
        await self._players.revoke_status_tag(player_id, self._kind.tag)

    async def expire(self, player_id: str, ctx: ToolContext | None) -> None:
        """인증 실패(쿠키 만료) 처리: 상태 태그를 떼고 만료 이벤트를 낸다.
        이벤트의 대상은 쿠키 주인(player_id)이다 — 최근 기록 조회(report.recent)처럼 요청한 사람과 쿠키 주인이 다를
        수 있으므로 ctx.emit(요청한 사람 기준) 대신 버스에 대상 플레이어를 직접 넘긴다."""
        await self._players.revoke_status_tag(player_id, self._kind.tag)
        if ctx is not None:
            await ctx.event_bus.emit_notify(
                self._kind.expired_event, {"player_id": player_id},
                player_id=player_id, guild_id=ctx.guild_id,
            )
