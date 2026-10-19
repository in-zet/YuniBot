# services/common/notify.py
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

from core.threads import (
    ClosedReason,
    ThreadManager,
    ThreadSession,
    ThreadTranscript,
    ThreadTransport,
)
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

    async def send_channel_message(self, channel_id: str, content: str, *, view: Any | None = None) -> None: ...
    # DM 채널을 열고(이미 있으면 그대로) 그 디스코드 채널 id를 돌려준다.
    async def open_dm_channel(self, discord_id: str) -> str: ...
    async def send_dm(self, discord_id: str, content: str, *, view: Any | None = None) -> None: ...
    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None: ...


class _ChannelTransport:
    """ThreadTransport 구현 — 디스코드 채널(여기서는 DM)에 글을 보낸다."""

    def __init__(self, gateway: DiscordGateway, channel_id: str) -> None:
        self._gateway = gateway
        self._channel_id = channel_id

    async def send_message(self, text: str) -> None:
        await self._gateway.send_channel_message(self._channel_id, text)


class NotifyService:
    def __init__(
        self,
        *,
        gateway: DiscordGateway,
        players: PlayersService,
        settings: SettingsService,
        threads: ThreadManager,
        end_button: Callable[[ThreadSession], Any] | None = None,
    ) -> None:
        self._gateway = gateway
        self._players = players
        self._settings = settings
        self._threads = threads
        # 대화 첫 메시지에 붙일 "끝내기" 버튼을 만드는 함수. 디스코드 UI는 코어(core/bot.py)가 안다.
        self._end_button = end_button

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
        channel_id: str,
        on_close: Callable[[ThreadTranscript], None | Awaitable[None]],
        idle_timeout: float = 300.0,
        close_timeout: float = 300.0,
        guidance: str = "",
        now: datetime,
    ) -> ThreadSession:
        return await self._threads.open(
            skill_name=skill_name, player_id=player_id, transport=transport, channel_id=channel_id, on_close=on_close,
            idle_timeout=idle_timeout, close_timeout=close_timeout, guidance=guidance, now=now,
        )

    async def open_dm_thread(
        self,
        *,
        skill_name: str,
        player_id: str,
        opening: str,
        on_close: Callable[[ThreadTranscript], None | Awaitable[None]],
        idle_timeout: float = 300.0,
        close_timeout: float = 300.0,
        guidance: str = "",
        now: datetime,
    ) -> ThreadSession | None:
        """플레이어의 대표 계정 DM에서 대화를 연다. 첫 말(opening)에는 끝내기 버튼이 붙는다.
        DM을 보낼 수 없거나(계정 없음·DM 차단) 이미 이 스킬로 연 대화가 있으면 None."""
        player = await self._players.get_player(player_id)
        if player is None or player.primary_discord_id is None:
            return None
        if self._threads.open_for(player_id, skill_name) is not None:
            return None
        channel_id = await self._gateway.open_dm_channel(player.primary_discord_id)
        session = await self.open_thread(
            skill_name=skill_name, player_id=player_id, transport=_ChannelTransport(self._gateway, channel_id),
            channel_id=channel_id, on_close=on_close, idle_timeout=idle_timeout, close_timeout=close_timeout,
            guidance=guidance, now=now,
        )
        try:
            view = self._end_button(session) if self._end_button is not None else None
            await self._gateway.send_channel_message(channel_id, opening, view=view)
        except Exception:
            self._threads.discard(session.thread_id)
            raise
        session.record_yuni(opening, at=now)
        return session

    def has_open_thread(self, player_id: str, skill_name: str) -> bool:
        return self._threads.open_for(player_id, skill_name) is not None

    async def close_thread(self, thread_id: str, *, reason: ClosedReason) -> None:
        await self._threads.close(thread_id, reason=reason)
