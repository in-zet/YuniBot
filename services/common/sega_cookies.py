# services/common/sega_cookies.py
"""`clal`(SEGA 공통 인증 쿠키) 저장. 게임마다 있는 NET 서비스(CHUNITHM·maimai)가 함께 쓴다. `clal`은 게임 공통이라
`sega_cookies` 테이블 하나에 있다. 틀은 `account_cookies.AccountCookieStore`다."""

from __future__ import annotations

import aiosqlite

from services.common.account_cookies import (
    SEGA_ACCOUNT,
    AccountCookieStore,
    LoginStatus,
)
from services.common.players import PlayersService
from services.common.secrets import SecretsService

__all__ = ["LoginStatus", "SegaCookieStore"]


class SegaCookieStore(AccountCookieStore):
    def __init__(self, *, conn: aiosqlite.Connection, secrets: SecretsService, players: PlayersService) -> None:
        super().__init__(conn=conn, secrets=secrets, players=players, kind=SEGA_ACCOUNT)

    async def clal(self, player_id: str) -> str | None:
        return await self.cookie(player_id)
