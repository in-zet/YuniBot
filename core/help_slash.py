# core/help_slash.py
from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from core.capabilities import build_capabilities_text
from core.permissions import TagProvider
from core.registry import SkillRegistry


def build_help_command(
    *, registry: SkillRegistry, tag_provider: TagProvider | None = None
) -> app_commands.Command[Any, Any, Any]:
    """LLM 없이 같은 안내를 보여주는 슬래시 명령. list_capabilities(core/core_tools.py)와
    core/capabilities.py의 같은 build_capabilities_text를 쓴다(00-core.md: "슬래시 /도움말도
    같은 목록을 LLM 없이 보여 준다")."""

    async def callback(interaction: discord.Interaction) -> None:
        player_tags: set[str] = set()
        if tag_provider is not None:
            player_tags = await tag_provider.tags_for(str(interaction.user.id))
        text = build_capabilities_text(
            registry=registry, player_tags=player_tags, tag_definitions=registry.tag_definitions(), category=None,
        )
        await interaction.response.send_message(text, ephemeral=True)

    return app_commands.Command(name="도움말", description="유니가 할 수 있는 일을 보여준다", callback=callback)
