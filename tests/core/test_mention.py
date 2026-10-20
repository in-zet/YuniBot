from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.agent import AgentLoop
from core.cooldown import CooldownTracker
from core.events import EventBus
from core.executor import ToolExecutor
from core.fallback_messages import COOLDOWN_ACTIVE_MESSAGES
from core.llm import GenerationTurn, LlmContent, LlmPart
from core.mention import (
    ChannelMessage,
    MentionEntry,
    build_conversation_context,
    is_mention_eligible,
)
from core.permissions import InMemoryTagProvider
from core.prompt import PromptAssembler
from core.registry import SkillRegistry
from core.resolvers import ResolverRegistry
from services.common.notify import NotifyService
from tests.support.fake_llm import FakeLlmAdapter


def _msg(id_: str, *, author: str, is_bot: bool = False, content: str = "", ref: str | None = None, mentions_bot: bool = False) -> ChannelMessage:
    return ChannelMessage(message_id=id_, author_discord_id=author, is_bot=is_bot, content=content, reference_id=ref, mentions_bot=mentions_bot)


def test_is_mention_eligible_true_for_direct_mention() -> None:
    trigger = _msg("1", author="p1", mentions_bot=True)
    assert is_mention_eligible(trigger=trigger, bot_discord_id="bot") is True


def test_is_mention_eligible_false_for_unrelated_message() -> None:
    trigger = _msg("1", author="p1", mentions_bot=False)
    assert is_mention_eligible(trigger=trigger, bot_discord_id="bot") is False


def test_is_mention_eligible_false_for_bots_own_message() -> None:
    trigger = _msg("1", author="bot", is_bot=True, mentions_bot=True)
    assert is_mention_eligible(trigger=trigger, bot_discord_id="bot") is False


def test_build_conversation_context_follows_reply_chain() -> None:
    m1 = _msg("1", author="p1", content="첫 질문", mentions_bot=True)
    m2 = _msg("2", author="bot", is_bot=True, content="첫 답", ref="1")
    m3 = _msg("3", author="p1", content="그럼 이건?", ref="2")
    context = build_conversation_context([m1, m2, m3], trigger=m3, player_discord_ids={"p1"})
    assert [m.message_id for m in context] == ["1", "2", "3"]


def test_build_conversation_context_stops_when_chain_breaks() -> None:
    m1 = _msg("1", author="p1", content="상관없는 글")
    m2 = _msg("2", author="p1", content="그럼 이건?", mentions_bot=True)  # 답장이 아니라 새 멘션
    context = build_conversation_context([m1, m2], trigger=m2, player_discord_ids={"p1"})
    # m2는 답장이 아니므로(reference_id=None) 사슬 거슬러 오르기가 아니라 최근 채널 기록
    # 폴백으로 간다 — 이 플레이어 자신의 과거 메시지(m1)도 "이 플레이어가 보낸" 메시지라
    # 맥락에 포함된다(test_build_conversation_context_caps_at_max_messages이 같은 원칙을
    # 봇 메시지가 하나도 없는 9개 이력에서도 확인한다).
    assert [m.message_id for m in context] == ["1", "2"]


def test_build_conversation_context_falls_back_to_recent_channel_history() -> None:
    m1 = _msg("1", author="p1", content="예전 질문")
    m2 = _msg("2", author="bot", is_bot=True, content="예전 답")
    m3 = _msg("3", author="other", content="상관없는 다른 사람 대화")
    m4 = _msg("4", author="p1", content="새 멘션", mentions_bot=True)
    context = build_conversation_context([m1, m2, m3, m4], trigger=m4, player_discord_ids={"p1"})
    assert [m.message_id for m in context] == ["1", "2", "4"]  # 다른 사람(other) 메시지는 빠진다


def test_build_conversation_context_caps_at_max_player_messages() -> None:
    history = [_msg(str(i), author="p1", content=f"{i}") for i in range(1, 10)]
    trigger = _msg("10", author="p1", content="마지막", mentions_bot=True)
    context = build_conversation_context(
        history + [trigger], trigger=trigger, player_discord_ids={"p1"}, max_player_messages=3
    )
    assert len(context) == 3
    assert context[-1].message_id == "10"


def test_build_conversation_context_does_not_count_yuni_messages() -> None:
    # 유니가 한 질문에 세 메시지(나눠 보낸 긴 표 등)로 답해도 플레이어 한 턴으로 친다.
    messages = [
        _msg("1", author="p1", content="질문"),
        _msg("2", author="bot", is_bot=True, content="답 1"),
        _msg("3", author="bot", is_bot=True, content="답 2"),
        _msg("4", author="bot", is_bot=True, content="답 3"),
        _msg("5", author="p1", content="다음 질문", mentions_bot=True),
    ]
    context = build_conversation_context(
        messages, trigger=messages[-1], player_discord_ids={"p1"}, max_player_messages=2
    )
    assert [m.message_id for m in context] == ["1", "2", "3", "4", "5"]


def test_build_conversation_context_drops_yuni_answers_of_cut_question() -> None:
    messages = [
        _msg("1", author="p1", content="오래된 질문"),
        _msg("2", author="bot", is_bot=True, content="오래된 답"),
        _msg("3", author="p1", content="질문"),
        _msg("4", author="bot", is_bot=True, content="답"),
        _msg("5", author="p1", content="새 멘션", mentions_bot=True),
    ]
    context = build_conversation_context(
        messages, trigger=messages[-1], player_discord_ids={"p1"}, max_player_messages=2
    )
    assert [m.message_id for m in context] == ["3", "4", "5"]


def test_build_conversation_context_reply_chain_counts_only_player_messages() -> None:
    m1 = _msg("1", author="p1", content="오래된 질문", mentions_bot=True)
    m2 = _msg("2", author="bot", is_bot=True, content="오래된 답", ref="1")
    m3 = _msg("3", author="p1", content="질문", ref="2")
    m4 = _msg("4", author="bot", is_bot=True, content="답 1", ref="3")
    m5 = _msg("5", author="bot", is_bot=True, content="답 2", ref="4")
    m6 = _msg("6", author="p1", content="그럼 이건?", ref="5")
    context = build_conversation_context(
        [m1, m2, m3, m4, m5, m6], trigger=m6, player_discord_ids={"p1"}, max_player_messages=2
    )
    assert [m.message_id for m in context] == ["3", "4", "5", "6"]


class _FakePlayers:
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
        return set()

    async def create_player(self):
        raise AssertionError("이 테스트에서는 player_for_discord가 항상 찾아내야 한다")

    async def link_account(self, player_id: str, discord_id: str) -> None:
        raise AssertionError("이 테스트에서는 link_account가 불릴 일이 없다")


class _FakeMemory:
    async def get(self, player_id: str) -> str:
        return ""

    async def set(self, player_id: str, content: str) -> None:
        return None

    async def enqueue(self, player_id: str, **kwargs) -> bool:
        return True


class _FakeUsage:
    async def month_total_usd(self, *, guild_id: str, year_month: str) -> float:
        return 0.0

    async def record(self, **kwargs) -> float:
        return 0.0


class _FakeSettings:
    async def get_value(self, *, scope, scope_id, skill, name, default):
        return default

    async def is_skill_enabled(self, *, guild_id, skill):
        return True


def _agent(script) -> AgentLoop:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(),
    )
    prompt = PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry)
    return AgentLoop(
        llm=FakeLlmAdapter(script), executor=executor, prompt=prompt,
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )


@pytest.mark.asyncio
async def test_mention_entry_sends_assembled_text() -> None:
    script = [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답이다")]), text="답이다")]
    entry = MentionEntry(
        agent=_agent(script),
        players=_FakePlayers(), notify=NotifyService.__new__(NotifyService),  # notify 미사용 경로만 테스트
        cooldown=CooldownTracker(), settings=_FakeSettings(), usage=_FakeUsage(),
        bot_discord_id="bot", agent_model="gemini-3.8-flash", memory=_FakeMemory(), event_bus=EventBus(),
        known_skill_names={"chunithm-border"}, skill_loading_mode="instructions",
    )
    sent: list[str] = []

    class _NoopTyping:
        async def __aenter__(self): return None
        async def __aexit__(self, *exc): return None

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="질문", mentions_bot=True)
    await entry.handle(
        trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC),
        send=lambda text, displays: sent.append(text) or _noop(), typing=_NoopTyping(),
    )
    assert sent == ["답이다"]


async def _noop() -> None:
    return None


@pytest.mark.asyncio
async def test_mention_entry_cooldown_blocks_without_calling_llm() -> None:
    script = [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="호출됨")]), text="호출됨")]
    agent = _agent(script)
    entry = MentionEntry(
        agent=agent, players=_FakePlayers(), notify=NotifyService.__new__(NotifyService),
        cooldown=CooldownTracker(), settings=_FakeSettings(), usage=_FakeUsage(), bot_discord_id="bot",
        agent_model="gemini-3.8-flash", memory=_FakeMemory(), event_bus=EventBus(),
        known_skill_names={"chunithm-border"}, skill_loading_mode="instructions",
    )
    sent: list[str] = []

    class _NoopTyping:
        async def __aenter__(self): return None
        async def __aexit__(self, *exc): return None

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="질문", mentions_bot=True)
    now = datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=now, send=lambda t, d: sent.append(t) or _noop(), typing=_NoopTyping())
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=now, send=lambda t, d: sent.append(t) or _noop(), typing=_NoopTyping())

    assert sent[0] == "호출됨"
    assert sent[1] in COOLDOWN_ACTIVE_MESSAGES
