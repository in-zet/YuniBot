# core/help_slash.py
from __future__ import annotations

from typing import Any, Protocol

import discord
from discord import app_commands

from core.capabilities import (
    CATEGORY_BLURBS,
    Capabilities,
    build_help_category,
    build_help_overview,
    collect_capabilities,
    help_categories,
)
from core.registry import SkillRegistry

_OVERVIEW_VALUE = "__overview__"
_MENU_TIMEOUT = 300


class _PlayerLookup(Protocol):
    """tags_for(player_id)는 플레이어 id를 받는다 — 디스코드 id가 아니다. 그래서 디스코드
    사용자를 플레이어로 먼저 찾는 player_for_discord도 같이 구조적으로 요구한다.
    PlayersService가 둘 다 있어 그대로 맞는다."""

    async def player_for_discord(self, discord_id: str) -> Any: ...
    async def tags_for(self, player_id: str) -> set[str]: ...


class HelpView(discord.ui.View):
    """분류를 고르면 같은 메시지가 그 분류의 안내로 바뀐다(00-core.md "할 수 있는 일 안내")."""

    def __init__(self, found: Capabilities) -> None:
        super().__init__(timeout=_MENU_TIMEOUT)
        self._found = found
        options = [discord.SelectOption(label="처음으로", value=_OVERVIEW_VALUE)]
        options.extend(
            discord.SelectOption(label=cat, value=cat, description=CATEGORY_BLURBS[cat][:100])
            for cat in help_categories(found)
        )
        select: discord.ui.Select[HelpView] = discord.ui.Select(placeholder="분류를 골라 줘", options=options)
        select.callback = self._on_select  # pyright: ignore[reportAttributeAccessIssue]
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        data: Any = interaction.data
        value = str(data["values"][0]) if data else _OVERVIEW_VALUE
        text = (
            build_help_overview(self._found)
            if value == _OVERVIEW_VALUE
            else build_help_category(self._found, value)
        )
        await interaction.response.edit_message(content=text, view=self)


def build_help_command(
    *, registry: SkillRegistry, players: _PlayerLookup | None = None
) -> app_commands.Command[Any, Any, Any]:
    """LLM 없이 같은 안내를 보여주는 슬래시 명령. 카드 목록은 list_capabilities와 같은
    collect_capabilities에서 모은다(00-core.md: "슬래시 /도움말도 같은 목록을 LLM 없이 보여 준다")."""

    async def callback(interaction: discord.Interaction) -> None:
        player_tags: set[str] = set()
        if players is not None:
            # tags_for는 플레이어 id를 받는다 — interaction.user.id(디스코드 id)를 그대로
            # 넣으면 거의 항상 매치되는 플레이어가 없어 태그가 늘 빈 집합으로 보인다.
            # 디스코드 사용자를 먼저 플레이어로 찾은 뒤, 그 id로 태그를 조회한다. 아직 한
            # 번도 플레이어가 안 생긴 신규 사용자는 빈 태그(로그아웃 상태 화면)로 둔다 —
            # 여기서 플레이어를 새로 만들지는 않는다(생성은 실제 명령/멘션 경로의 일이다).
            player = await players.player_for_discord(str(interaction.user.id))
            if player is not None:
                player_tags = await players.tags_for(player.id)
        found = collect_capabilities(registry=registry, player_tags=player_tags, category=None)
        await interaction.response.send_message(
            build_help_overview(found), view=HelpView(found), ephemeral=True
        )

    return app_commands.Command(name="도움말", description="유니가 할 수 있는 일을 보여준다", callback=callback)
