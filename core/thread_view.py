# core/thread_view.py
from __future__ import annotations

from typing import Any

import discord

from core.threads import ThreadManager, ThreadSession
from services.common.players import PlayersService

ENDED_MESSAGE = "대화를 마쳤어."


class ThreadEndView(discord.ui.View):
    """대화 스레드(DM)의 "끝내기" 버튼. 판단 없이 바로 닫는다. 대화의 플레이어 본인만 누를 수 있다."""

    def __init__(self, *, threads: ThreadManager, players: PlayersService, session: ThreadSession) -> None:
        # 대화가 닫히면 세션이 사라지므로 버튼도 쓸모가 없다 — 시간 제한은 두지 않는다.
        super().__init__(timeout=None)
        self._threads = threads
        self._players = players
        self._thread_id = session.thread_id
        self._player_id = session.player_id

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        player = await self._players.player_for_discord(str(interaction.user.id))
        return player is not None and player.id == self._player_id

    @discord.ui.button(label="대화 끝내기", style=discord.ButtonStyle.secondary)
    async def end_button(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        await interaction.response.edit_message(view=None)
        await self._threads.close(self._thread_id, reason="explicit")
        await interaction.followup.send(ENDED_MESSAGE)
