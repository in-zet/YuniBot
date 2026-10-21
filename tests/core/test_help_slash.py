from pathlib import Path
from typing import Any

import pytest

from core.help_slash import build_help_command
from core.registry import SkillRegistry


def test_build_help_command_has_korean_name() -> None:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    command = build_help_command(registry=registry)
    assert command.name == "도움말"


class _FakeInteractionUser:
    def __init__(self, discord_id: int) -> None:
        self.id = discord_id


class _FakeResponse:
    def __init__(self) -> None:
        self.sent: dict[str, Any] | None = None

    async def send_message(self, text: str, *, ephemeral: bool = False, view: Any = None) -> None:
        self.sent = {"text": text, "ephemeral": ephemeral, "view": view}


class _FakeInteraction:
    def __init__(self, discord_id: int) -> None:
        self.user = _FakeInteractionUser(discord_id)
        self.response = _FakeResponse()


def _fake(discord_id: int) -> Any:
    # discord.Interaction 자리에 넘기므로 타입 검사기에는 Any로 보인다(tests/core/test_slash.py의
    # _fake()와 같은 관례).
    return _FakeInteraction(discord_id)


class _FakePlayer:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _FakePlayers:
    """회귀: player_for_discord로 먼저 player를 찾고, 그 player.id로 tags_for를
    불러야 한다. interaction.user.id(디스코드 id)를 바로 tags_for에 넣으면 이 가짜는
    항상 빈 집합을 돌려줘 admin 카드가 숨는다."""

    def __init__(self) -> None:
        self.tags_for_calls: list[str] = []

    async def player_for_discord(self, discord_id: str):
        if discord_id == "111":
            return _FakePlayer("p1")
        return None

    async def tags_for(self, player_id: str) -> set[str]:
        self.tags_for_calls.append(player_id)
        if player_id == "p1":
            return {"admin"}
        return set()


@pytest.mark.asyncio
async def test_help_command_resolves_player_before_looking_up_tags() -> None:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    players = _FakePlayers()
    command = build_help_command(registry=registry, players=players)

    interaction = _fake(111)
    await command.callback(interaction)  # pyright: ignore[reportCallIssue]

    # tags_for가 디스코드 id("111")가 아니라 해석된 player id("p1")로 불렸는지가 핵심이다.
    assert players.tags_for_calls == ["p1"]


@pytest.mark.asyncio
async def test_help_command_falls_back_to_empty_tags_for_unknown_discord_user() -> None:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    players = _FakePlayers()
    command = build_help_command(registry=registry, players=players)

    real_interaction = _FakeInteraction(999)  # player_for_discord가 None을 돌려주는 신규 사용자
    await command.callback(real_interaction)  # pyright: ignore[reportCallIssue, reportArgumentType]

    assert players.tags_for_calls == []
    assert real_interaction.response.sent is not None


class _FakeEditResponse(_FakeResponse):
    def __init__(self) -> None:
        super().__init__()
        self.edited: dict[str, Any] | None = None

    async def edit_message(self, *, content: str, view: Any) -> None:
        self.edited = {"content": content, "view": view}


@pytest.mark.asyncio
async def test_help_menu_switches_the_same_message_to_the_chosen_category() -> None:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    command = build_help_command(registry=registry, players=_FakePlayers())
    interaction = _FakeInteraction(999)
    interaction.response = _FakeEditResponse()
    await command.callback(interaction)  # pyright: ignore[reportCallIssue, reportArgumentType]

    assert interaction.response.sent is not None
    assert interaction.response.sent["ephemeral"] is True
    view = interaction.response.sent["view"]
    assert view is not None

    interaction.data = {"values": ["곡 정보"]}  # pyright: ignore[reportAttributeAccessIssue]
    await view._on_select(interaction)  # pyright: ignore[reportPrivateUsage]
    assert interaction.response.edited is not None
    assert "곡 정보" in interaction.response.edited["content"]

    interaction.data = {"values": ["__overview__"]}  # pyright: ignore[reportAttributeAccessIssue]
    await view._on_select(interaction)  # pyright: ignore[reportPrivateUsage]
    assert "유니" in interaction.response.edited["content"]
