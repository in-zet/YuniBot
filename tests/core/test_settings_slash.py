# tests/core/test_settings_slash.py
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiosqlite
import discord
import pytest

from core.registry import SkillRegistry
from core.settings_slash import build_settings_group
from services.common.players import PlayersService
from services.common.settings import SettingsService

_CORE_MIGRATIONS = [
    Path("migrations/core/001_players.sql").read_text(),
    Path("migrations/core/002_player_tags.sql").read_text(),
    Path("migrations/core/003_settings.sql").read_text(),
]


class _FakeResponse:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, content: str | None = None, **kwargs: Any) -> None:
        self.calls.append({"content": content, **kwargs})


class _FakeInteraction:
    def __init__(self, user_id: int, *, guild_id: int | None = None) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.guild_id = guild_id
        self.response = _FakeResponse()


@pytest.fixture
async def settings_env():
    conn = await aiosqlite.connect(":memory:")
    for migration in _CORE_MIGRATIONS:
        await conn.executescript(migration)
    await conn.commit()
    players = PlayersService(conn)
    settings = SettingsService(conn)
    registry = SkillRegistry([Path("tests/fixtures/skills_ok/_with_settings")])
    registry.load()
    assert registry.failures() == {}
    group = build_settings_group(registry=registry, settings=settings, players=players)
    try:
        yield group, players, settings
    finally:
        await conn.close()


async def test_view_shows_default_before_any_change(settings_env) -> None:
    group, players, _ = settings_env
    player = await players.create_player()
    await players.link_account(player.id, "1")

    view = group.get_command("보기")
    assert isinstance(view, discord.app_commands.Command)
    interaction = _FakeInteraction(1)

    await view.callback(interaction)  # pyright: ignore[reportArgumentType, reportCallIssue]

    [call] = interaction.response.calls
    assert "with-settings" in call["content"]
    assert "True" in call["content"]  # 기본값


async def test_change_updates_player_scoped_value(settings_env) -> None:
    group, players, settings = settings_env
    player = await players.create_player()
    await players.link_account(player.id, "1")

    change = group.get_command("바꾸기")
    assert isinstance(change, discord.app_commands.Command)
    interaction = _FakeInteraction(1)

    await change.callback(
        interaction, skill="with-settings", setting="알림", value="false"  # pyright: ignore[reportArgumentType, reportCallIssue]
    )

    assert "바꿨다" in interaction.response.calls[0]["content"]
    value = await settings.get_value(
        scope="player", scope_id=player.id, skill="with-settings", name="알림", default=True
    )
    assert value is False


async def test_change_guild_scoped_requires_admin_tag(settings_env) -> None:
    group, players, _ = settings_env
    player = await players.create_player()
    await players.link_account(player.id, "1")

    change = group.get_command("바꾸기")
    assert isinstance(change, discord.app_commands.Command)
    interaction = _FakeInteraction(1, guild_id=999)

    await change.callback(
        interaction, skill="with-settings", setting="기준일", value="30"  # pyright: ignore[reportArgumentType, reportCallIssue]
    )

    assert "관리자만" in interaction.response.calls[0]["content"]

    await players.grant_tag(player.id, "admin", source="test")
    interaction2 = _FakeInteraction(1, guild_id=999)
    await change.callback(
        interaction2, skill="with-settings", setting="기준일", value="30"  # pyright: ignore[reportArgumentType, reportCallIssue]
    )
    assert "바꿨다" in interaction2.response.calls[0]["content"]


async def test_account_link_mention_setting_is_reachable_through_settings_command() -> None:
    """리뷰(최종) Fix 6: notify.mention이 읽는 account-link의 '알림을 받을 계정'은 manifest에
    선언돼야 /설정으로 보이고 바꿀 수 있다."""
    conn = await aiosqlite.connect(":memory:")
    for migration in _CORE_MIGRATIONS:
        await conn.executescript(migration)
    await conn.commit()
    players = PlayersService(conn)
    settings = SettingsService(conn)
    registry = SkillRegistry([Path("skills/account-link")])
    registry.load()
    assert registry.failures() == {}
    group = build_settings_group(registry=registry, settings=settings, players=players)
    player = await players.create_player()
    await players.link_account(player.id, "1")

    try:
        view = group.get_command("보기")
        assert isinstance(view, discord.app_commands.Command)
        shown = _FakeInteraction(1)
        await view.callback(shown)  # pyright: ignore[reportArgumentType, reportCallIssue]
        assert "account-link" in shown.response.calls[0]["content"]
        assert "알림을 받을 계정" in shown.response.calls[0]["content"]
        assert "primary" in shown.response.calls[0]["content"]

        change = group.get_command("바꾸기")
        assert isinstance(change, discord.app_commands.Command)
        changed = _FakeInteraction(1)
        await change.callback(
            changed, skill="account-link", setting="알림을 받을 계정", value="all"  # pyright: ignore[reportArgumentType, reportCallIssue]
        )
        assert "바꿨다" in changed.response.calls[0]["content"]
        assert await settings.get_value(
            scope="player", scope_id=player.id, skill="account-link", name="알림을 받을 계정", default="primary"
        ) == "all"
    finally:
        await conn.close()
