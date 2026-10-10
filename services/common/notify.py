# services/common/notify.py
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

from core.threads import ThreadManager, ThreadSession, ThreadTranscript, ThreadTransport
from services.common.players import PlayersService
from services.common.settings import SettingsService

_MENTION_SETTING_SKILL = "account-link"
_MENTION_SETTING_NAME = "알림을 받을 계정"


class DiscordGateway(Protocol):
    """core/bot.py가 실제 discord.Client로 구현을 채운다. 서비스는 디스코드 API를 직접
    모른다 — 이 프로토콜이 유일한 창구다."""

    async def send_channel_message(self, channel_id: str, content: str) -> None: ...
    async def send_dm(self, discord_id: str, content: str) -> None: ...


class NotifyService:
    def __init__(
        self,
        *,
        gateway: DiscordGateway,
        players: PlayersService,
        settings: SettingsService,
        threads: ThreadManager,
    ) -> None:
        self._gateway = gateway
        self._players = players
        self._settings = settings
        self._threads = threads

    async def post(self, channel_id: str, content: str) -> None:
        await self._gateway.send_channel_message(channel_id, content)

    async def dm(self, player_id: str, content: str) -> None:
        player = await self._players.get_player(player_id)
        if player is None or player.primary_discord_id is None:
            return
        await self._gateway.send_dm(player.primary_discord_id, content)

    async def mention(self, player_id: str, content: str) -> str:
        """알림 멘션 설정(account-link의 '알림을 받을 계정')을 반영해 멘션 문자열을 만든다."""
        target = await self._settings.get_value(
            scope="player", scope_id=player_id, skill=_MENTION_SETTING_SKILL,
            name=_MENTION_SETTING_NAME, default="primary",
        )
        player = await self._players.get_player(player_id)
        if player is None:
            raise ValueError("모르는 플레이어다")

        if target == "all":
            accounts = await self._players.accounts_for(player_id)
            mentions = " ".join(f"<@{a.discord_id}>" for a in accounts)
            return f"{mentions} {content}" if mentions else content

        discord_id = player.primary_discord_id if target in ("primary", "") else target
        return f"<@{discord_id}> {content}" if discord_id else content

    async def open_thread(
        self,
        *,
        skill_name: str,
        player_id: str,
        transport: ThreadTransport,
        on_close: Callable[[ThreadTranscript], None | Awaitable[None]],
        idle_timeout: float = 300.0,
        close_timeout: float = 300.0,
        now: datetime,
    ) -> ThreadSession:
        return await self._threads.open(
            skill_name=skill_name, player_id=player_id, transport=transport, on_close=on_close,
            idle_timeout=idle_timeout, close_timeout=close_timeout, now=now,
        )
