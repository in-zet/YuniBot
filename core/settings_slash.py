# core/settings_slash.py
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import discord
from discord import app_commands

from core.manifest import SettingSpec
from core.permissions import TagRequirement, check_tags
from core.registry import SkillRegistry
from core.tool_types import DEFAULT_GAME, UserFacingError
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


# 스킬이 아니라 플레이어 자체에 딸린 설정. 값은 settings_values가 아니라 players 칸에 있다(실행기가 바로 읽는다).
_PLAYER_SKILL = "player"
_MAIN_GAME_SETTING = "주 게임"
_MAIN_GAME_HELP = "게임을 말하지 않았을 때 쓰는 게임(예: 마이마이, 츄니즘). 비우려면 '없음'"
_UNSET_WORDS = frozenset({"없음", "비우기", "초기화"})


def build_settings_group(
    *,
    registry: SkillRegistry,
    settings: SettingsService,
    players: PlayersService,
    game_resolver: Callable[[str], str] | None = None,
) -> app_commands.Group:
    """`game_resolver`는 게임 이름(별칭 포함)을 등록된 게임으로 바꾼다. 주면 `주 게임` 설정이 생긴다."""
    group = app_commands.Group(name="설정", description="플레이어·서버 설정을 보고 바꾼다")

    @group.command(name="보기", description="내 설정과(서버라면) 서버 설정을 본다")
    async def view(interaction: discord.Interaction) -> None:
        player = await players.player_for_discord(str(interaction.user.id))
        player_id = player.id if player else None
        guild_id = str(interaction.guild_id) if interaction.guild_id else None

        lines: list[str] = []
        if game_resolver is not None and player is not None:
            main_game = player.main_game or f"정하지 않음({DEFAULT_GAME})"
            lines.append(f"[{_PLAYER_SKILL}] {_MAIN_GAME_SETTING} (player) = {main_game!r} — {_MAIN_GAME_HELP}")
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
        if game_resolver is not None and (skill, setting) == (_PLAYER_SKILL, _MAIN_GAME_SETTING):
            await _change_main_game(interaction, value, game_resolver)
            return
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

    async def _change_main_game(
        interaction: discord.Interaction, value: str, resolve: Callable[[str], str]
    ) -> None:
        player = await players.player_for_discord(str(interaction.user.id))
        if player is None:
            await interaction.response.send_message("먼저 나한테 한 번 말을 걸어 줘.", ephemeral=True)
            return
        if value.strip() in _UNSET_WORDS:
            await players.set_main_game(player.id, "")
            await interaction.response.send_message("주 게임을 비웠어. 이제 게임을 말하지 않으면 기본 게임이야.", ephemeral=True)
            return
        try:
            game = resolve(value)
        except UserFacingError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await players.set_main_game(player.id, game)
        await interaction.response.send_message(f"주 게임을 {game}로 바꿨어.", ephemeral=True)

    return group
