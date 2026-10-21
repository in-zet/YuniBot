# core/settings_slash.py
from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from core.manifest import SettingSpec
from core.permissions import TagRequirement, check_tags
from core.registry import SkillRegistry
from services.common.players import PlayersService
from services.common.settings import SettingsService


def _coerce(kind: str, raw: str) -> Any:
    if kind == "bool":
        lowered = raw.strip().lower()
        if lowered in {"true", "1", "on", "켜짐", "예"}:
            return True
        if lowered in {"false", "0", "off", "꺼짐", "아니오"}:
            return False
        raise ValueError(f"'{raw}'를 켜짐/꺼짐으로 읽을 수 없어")
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    return raw


def _find_setting(registry: SkillRegistry, skill_name: str, setting_name: str) -> SettingSpec:
    for skill in registry.enabled_skills():
        if skill.manifest.name != skill_name:
            continue
        for spec in skill.manifest.settings:
            if spec.name == setting_name:
                return spec
        raise ValueError(f"'{skill_name}'에는 '{setting_name}' 설정이 없어")
    raise ValueError(f"'{skill_name}' 스킬을 찾지 못했어")


def build_settings_group(
    *, registry: SkillRegistry, settings: SettingsService, players: PlayersService
) -> app_commands.Group:
    group = app_commands.Group(name="설정", description="플레이어·서버 설정을 보고 바꾼다")

    @group.command(name="보기", description="내 설정과(서버라면) 서버 설정을 본다")
    async def view(interaction: discord.Interaction) -> None:
        player = await players.player_for_discord(str(interaction.user.id))
        player_id = player.id if player else None
        guild_id = str(interaction.guild_id) if interaction.guild_id else None

        lines: list[str] = []
        for skill in registry.enabled_skills():
            for spec in skill.manifest.settings:
                if spec.scope == "player" and player_id is None:
                    continue
                if spec.scope == "guild" and guild_id is None:
                    continue
                scope_id = player_id if spec.scope == "player" else guild_id
                assert scope_id is not None
                value = await settings.get_value(
                    scope=spec.scope,
                    scope_id=scope_id,
                    skill=skill.manifest.name,
                    name=spec.name,
                    default=spec.default,
                )
                lines.append(
                    f"[{skill.manifest.name}] {spec.name} ({spec.scope}) = {value!r} — {spec.description}"
                )

        text = "\n".join(lines) if lines else "설정할 수 있는 항목이 없어."
        await interaction.response.send_message(text, ephemeral=True)

    @group.command(name="바꾸기", description="설정값을 바꾼다")
    @app_commands.describe(skill="스킬 이름", setting="설정 이름", value="새 값")
    async def change(interaction: discord.Interaction, skill: str, setting: str, value: str) -> None:
        try:
            spec = _find_setting(registry, skill, setting)
            coerced = _coerce(spec.kind, value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        player = await players.player_for_discord(str(interaction.user.id))
        if player is None:
            await interaction.response.send_message(
                "먼저 나한테 한 번 말을 걸어 줘.", ephemeral=True
            )
            return

        if spec.scope == "player":
            scope_id = player.id
        else:
            if interaction.guild_id is None:
                await interaction.response.send_message(
                    "서버 설정은 서버 안에서만 바꿀 수 있어.", ephemeral=True
                )
                return
            tags = await players.tags_for(player.id)
            if not check_tags(TagRequirement(tags=["admin"]), tags):
                await interaction.response.send_message(
                    "서버 설정은 관리자만 바꿀 수 있어.", ephemeral=True
                )
                return
            scope_id = str(interaction.guild_id)

        await settings.set_value(
            scope=spec.scope, scope_id=scope_id, skill=skill, name=setting, value=coerced,
            updated_by=player.id,
        )
        await interaction.response.send_message(f"{skill}의 '{setting}'을 {coerced!r}로 바꿨어.", ephemeral=True)

    return group
