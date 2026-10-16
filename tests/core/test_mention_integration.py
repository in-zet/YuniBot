# tests/core/test_mention_integration.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.agent import AgentLoop
from core.cooldown import CooldownTracker
from core.executor import ToolExecutor
from core.llm import FunctionCallRequest, GenerationTurn, LlmContent, LlmPart, UsageInfo
from core.mention import ChannelMessage, MentionEntry
from core.permissions import InMemoryTagProvider
from core.prompt import PromptAssembler
from core.registry import SkillRegistry
from core.resolvers import ResolverRegistry
from services.common.notify import NotifyService
from tests.support.fake_llm import FakeLlmAdapter


class _FakePlayers:
    def __init__(self) -> None:
        self.tags: set[str] = set()

    async def player_for_discord(self, discord_id: str):
        from services.common.players import Player
        return Player(
            id="p1", primary_discord_id=discord_id, records_public=False, memos_public=False,
            goals_public=False, arcade_public=False, reviews_public=False, sync_report_public=True,
            first_seen_at="2026-01-01T00:00:00+00:00",
        )

    async def accounts_for(self, player_id: str):
        from services.common.players import DiscordAccount
        return [DiscordAccount(discord_id="p1", player_id="p1", is_primary=True, linked_at="2026-01-01T00:00:00+00:00")]

    async def tags_for(self, player_id: str) -> set[str]:
        return self.tags


class _FakeUsage:
    def __init__(self) -> None:
        self.recorded: list[dict[str, object]] = []

    async def month_total_usd(self, *, guild_id: str, year_month: str) -> float:
        return 0.0

    async def record(self, **kwargs) -> float:
        self.recorded.append(kwargs)
        return 0.0


class _FakeSettings:
    async def get_value(self, *, scope, scope_id, skill, name, default):
        return default

    async def is_skill_enabled(self, *, guild_id, skill):
        return True


class _NoopTyping:
    async def __aenter__(self): return None
    async def __aexit__(self, *exc): return None


def _registry() -> SkillRegistry:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    return registry


def _mention_entry(script, *, player_tags: set[str] | None = None) -> tuple[MentionEntry, _FakeUsage]:
    registry = _registry()
    executor = ToolExecutor(resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions=registry.tag_definitions())
    prompt = PromptAssembler(rules_text=Path("prompts/rules.md").read_text(), persona_text=Path("prompts/persona.md").read_text(), registry=registry)
    agent = AgentLoop(llm=FakeLlmAdapter(script), executor=executor, prompt=prompt, model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000)
    players = _FakePlayers()
    players.tags = player_tags if player_tags is not None else set()
    usage = _FakeUsage()
    entry = MentionEntry(
        agent=agent, players=players, notify=NotifyService.__new__(NotifyService), cooldown=CooldownTracker(),
        settings=_FakeSettings(), usage=usage, bot_discord_id="bot",
        known_skill_names={s.manifest.name for s in registry.enabled_skills()}, skill_loading_mode="instructions",
    )
    return entry, usage


@pytest.mark.asyncio
async def test_full_flow_open_skill_then_tool_call_and_display_shown_on_numeric_violation() -> None:
    call_lookup = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="chunithm-border_lookup", args={"chart": "크로노 MAS"})],
        usage=UsageInfo(input_tokens=100, output_tokens=5),
    )
    bad_answer = GenerationTurn(
        content=LlmContent(role="model", parts=[LlmPart(text="12345어택이다.")]),  # data에 없는 숫자
        text="12345어택이다.",
        usage=UsageInfo(input_tokens=50, output_tokens=5),
    )
    entry, usage = _mention_entry([call_lookup, bad_answer])
    sent: list[str] = []

    async def _collect(text: str) -> None:
        sent.append(text)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="크로노 마스 보더", mentions_bot=True)
    await entry.handle(
        trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC),
        send=_collect, typing=_NoopTyping(),
    )

    assert sent == ["12345어택이다."]
    assert usage.recorded[0]["input_tokens"] == 150
    assert usage.recorded[0]["output_tokens"] == 10


@pytest.mark.asyncio
async def test_full_flow_admin_tool_blocked_without_admin_tag() -> None:
    call_admin = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="admin_skills", args={})],
    )
    relay_error = None

    def script(model, system, contents, tools):
        nonlocal relay_error
        if len(contents) == 1:
            return call_admin
        for part in contents[-1].parts:
            if part.function_response is not None:
                relay_error = part.function_response.response.get("error")
        return GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="막혔다고 전달함")]), text="막혔다고 전달함")

    entry, _usage = _mention_entry(script, player_tags=set())
    sent: list[str] = []

    async def _collect(text: str) -> None:
        sent.append(text)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="관리 스킬 보여줘", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_collect, typing=_NoopTyping())

    assert relay_error is not None  # admin 태그 없는 플레이어 — "모르는 명령이에요" 류 오류가 모델에 전달됨
    assert sent == ["막혔다고 전달함"]
