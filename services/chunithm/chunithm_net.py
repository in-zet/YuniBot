# services/chunithm/chunithm_net.py
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import aiosqlite

from core.tool_types import ToolContext
from services.common.players import PlayersService
from services.common.secrets import SecretsService

if TYPE_CHECKING:
    from chuni_penguin.types import PersonalBest, Profile, RecentScore


class ChunithmNetAdapterLike(Protocol):
    async def get_profile(self) -> Profile: ...
    async def get_best30(self) -> list[PersonalBest]: ...
    async def get_new20(self) -> list[PersonalBest]: ...
    async def get_recent_scores(self) -> list[RecentScore]: ...
    async def logout(self) -> None: ...
    async def aclose(self) -> None: ...


AdapterFactory = Callable[[str], ChunithmNetAdapterLike]


def _default_adapter_factory(lwp_cookie_jar: str) -> ChunithmNetAdapterLike:
    from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter

    return ChunithmNetAdapter(database=None, discord_id=0, lwp_cookie_jar=lwp_cookie_jar)


@dataclass(slots=True, kw_only=True)
class LoginStatus:
    linked: bool
    nickname: str | None = None
    last_checked_at: str | None = None


@dataclass(slots=True, kw_only=True)
class SyncFetch:
    profile: Profile
    best30: list[PersonalBest]
    new20: list[PersonalBest]
    recent_scores: list[RecentScore]


class ChunithmNetService:
    """플레이어별로 CHUNITHM-NET 어댑터를 열어 준다. 복호화는 이 서비스 안에서만
    하고, 평문 쿠키는 어댑터를 만드는 그 함수 스코프를 벗어나지 않는다."""

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        secrets: SecretsService,
        players: PlayersService,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row
        self._secrets = secrets
        self._players = players
        self._adapter_factory = adapter_factory or _default_adapter_factory

    async def login(self, player_id: str, clal: str, ctx: ToolContext) -> Profile:
        """clal 값으로 프로필을 한 번 읽어 본다. 성공해야 저장하고 태그를 붙인다 —
        실패하면 아무것도 바꾸지 않는다(설계서: 실패면 이유를 본인에게만 알린다)."""
        lwp_cookie_jar = f"#LWP-Cookies-2.0\nSet-Cookie3: clal={clal}\n"
        adapter = self._adapter_factory(lwp_cookie_jar)
        try:
            profile = await adapter.get_profile()
        finally:
            await adapter.aclose()

        encrypted = self._secrets.encrypt(clal)
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO chunithm_net_cookies (player_id, encrypted_cookie, linked_at, last_checked_at, nickname) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (player_id) DO UPDATE SET "
            "encrypted_cookie = excluded.encrypted_cookie, linked_at = excluded.linked_at, "
            "last_checked_at = excluded.last_checked_at, nickname = excluded.nickname",
            (player_id, encrypted, now, now, profile.username),
        )
        await self._conn.commit()
        await self._players.set_status_tag(player_id, "chunithm-linked", source="chunithm_net:login")
        return profile

    async def status(self, player_id: str) -> LoginStatus:
        """상태 조회는 DB만 읽는 가벼운 호출이어야 한다 — 네트워크를 타지 않는다.
        닉네임은 login()이 저장해 둔 값을 그대로 돌려준다."""
        row = await (
            await self._conn.execute(
                "SELECT last_checked_at, nickname FROM chunithm_net_cookies WHERE player_id = ?",
                (player_id,),
            )
        ).fetchone()
        if row is None:
            return LoginStatus(linked=False)
        tags = await self._players.tags_for(player_id)
        if "chunithm-linked" not in tags:
            return LoginStatus(linked=False)
        return LoginStatus(
            linked=True, nickname=row["nickname"], last_checked_at=row["last_checked_at"]
        )

    async def logout(self, player_id: str, ctx: ToolContext) -> None:
        cookie = await self._decrypted_cookie(player_id)
        if cookie is not None:
            adapter = self._adapter_factory(cookie)
            try:
                await adapter.logout()
            finally:
                await adapter.aclose()
        await self._conn.execute(
            "DELETE FROM chunithm_net_cookies WHERE player_id = ?", (player_id,)
        )
        await self._conn.commit()
        await self._players.revoke_status_tag(player_id, "chunithm-linked")

    async def fetch_for_sync(self, player_id: str, *, ctx: ToolContext | None = None) -> SyncFetch:
        """동기화에 필요한 세 가지(프로필, 베스트30, 신곡20, 최근 이력)를 한 번에
        가져온다. 접속이 실패하면 만료로 보고 chunithm-linked를 떼고
        chunithm.login.expired를 낸 뒤 그대로 예외를 다시 던진다 — 호출한 쪽
        (5단계 chunithm-sync 스킬)이 "/로그인 안내" 오류로 바꾼다."""
        cookie = await self._decrypted_cookie(player_id)
        if cookie is None:
            raise ConnectionError("로그인되어 있지 않다")

        adapter = self._adapter_factory(cookie)
        try:
            profile = await adapter.get_profile()
            best30 = await adapter.get_best30()
            new20 = await adapter.get_new20()
            recent_scores = await adapter.get_recent_scores()
        except Exception:
            await self._players.revoke_status_tag(player_id, "chunithm-linked")
            if ctx is not None:
                await ctx.emit("chunithm.login.expired", {"player_id": player_id})
            raise
        finally:
            await adapter.aclose()

        return SyncFetch(profile=profile, best30=best30, new20=new20, recent_scores=recent_scores)

    async def _decrypted_cookie(self, player_id: str) -> str | None:
        """평문은 이 메서드를 부른 호출자 스코프에만 잠깐 존재한다 — 반환값을
        로그나 LLM 입력에 넣지 않는 것은 호출하는 쪽의 책임이다(CLAUDE.md)."""
        row = await (
            await self._conn.execute(
                "SELECT encrypted_cookie FROM chunithm_net_cookies WHERE player_id = ?", (player_id,)
            )
        ).fetchone()
        if row is None:
            return None
        decrypted = self._secrets.decrypt(row["encrypted_cookie"])
        return f"#LWP-Cookies-2.0\nSet-Cookie3: clal={decrypted}\n"
