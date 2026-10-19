# services/common/notify.py
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

from core.threads import ThreadManager, ThreadSession, ThreadTranscript, ThreadTransport
from services.common.players import PlayersService
from services.common.settings import SettingsService

_MENTION_SETTING_SKILL = "account-link"
_MENTION_SETTING_NAME = "알림을 받을 계정"

_CHANNEL_MENTION_RE = re.compile(r"^<#(\d{15,25})>$")
_BARE_CHANNEL_ID_RE = re.compile(r"^\d{15,25}$")


def parse_channel_ref(raw: str) -> str | None:
    """#채널 멘션(<#id>)이나 맨 숫자 id를 채널 id로 바꾼다. 못 읽으면 None. 여러 스킬이
    "채널을 설정값으로 받는" 같은 모양이라 공통 함수로 둔다(admin, chunithm-updates)."""
    text = raw.strip()
    mention = _CHANNEL_MENTION_RE.match(text)
    if mention:
        return mention.group(1)
    if _BARE_CHANNEL_ID_RE.match(text):
        return text
    return None


class DiscordGateway(Protocol):
    """core/bot.py가 실제 discord.Client로 구현을 채운다. 서비스는 디스코드 API를 직접
    모른다 — 이 프로토콜이 유일한 창구다."""

    async def send_channel_message(self, channel_id: str, content: str) -> None: ...
    async def send_dm(self, discord_id: str, content: str, *, view: Any | None = None) -> None: ...
    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None: ...


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

    async def dm(self, player_id: str, content: str, *, view: Any | None = None) -> None:
        player = await self._players.get_player(player_id)
        if player is None or player.primary_discord_id is None:
            return
        await self._gateway.send_dm(player.primary_discord_id, content, view=view)

    async def react(self, channel_id: str, message_id: str, emoji: str) -> None:
        await self._gateway.add_reaction(channel_id, message_id, emoji)

    async def mention(self, player_id: str, content: str) -> str:
        """알림 멘션 설정(account-link의 '알림을 받을 계정')을 반영해 멘션 문자열을 만든다."""
        target = await self._settings.get_value(
            scope="player", scope_id=player_id, skill=_MENTION_SETTING_SKILL,
            name=_MENTION_SETTING_NAME, default="primary",
        )
        player = await self._players.get_player(player_id)
        if player is None:
            raise ValueError("모르는 플레이어다")

        accounts = await self._players.accounts_for(player_id)
        if target == "all":
            mentions = " ".join(f"<@{a.discord_id}>" for a in accounts)
            return f"{mentions} {content}" if mentions else content

        # 특정 계정을 고른 값은 플레이어가 /설정으로 넣은 임의 문자열이다 — 그대로 멘션 문법에
        # 끼우면 멘션 밖으로 빠져나가는 값을 넣을 수 있다. 이 플레이어의 실제 연결 계정 ID일
        # 때만 쓰고, 아니면(오타, 해제된 계정, 조작된 값) 대표 계정으로 되돌아간다.
        own_ids = {a.discord_id for a in accounts}
        discord_id = target if isinstance(target, str) and target in own_ids else player.primary_discord_id
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
