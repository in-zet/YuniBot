# core/help_slash.py
from __future__ import annotations

from typing import Any, Protocol

import discord
from discord import app_commands

from core.capabilities import build_capabilities_text
from core.registry import SkillRegistry


class _PlayerLookup(Protocol):
    """tags_for(player_id)는 플레이어 id를 받는다 — 디스코드 id가 아니다. 그래서 디스코드
    사용자를 플레이어로 먼저 찾는 player_for_discord도 같이 구조적으로 요구한다.
    PlayersService가 둘 다 있어 그대로 맞는다."""

    async def player_for_discord(self, discord_id: str) -> Any: ...
    async def tags_for(self, player_id: str) -> set[str]: ...


def build_help_command(
    *, registry: SkillRegistry, players: _PlayerLookup | None = None
) -> app_commands.Command[Any, Any, Any]:
    """LLM 없이 같은 안내를 보여주는 슬래시 명령. list_capabilities(core/core_tools.py)와
    core/capabilities.py의 같은 build_capabilities_text를 쓴다(00-core.md: "슬래시 /도움말도
    같은 목록을 LLM 없이 보여 준다")."""

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
        text = build_capabilities_text(
            registry=registry, player_tags=player_tags, tag_definitions=registry.tag_definitions(), category=None,
        )
        await interaction.response.send_message(text, ephemeral=True)

    return app_commands.Command(name="도움말", description="유니가 할 수 있는 일을 보여준다", callback=callback)
