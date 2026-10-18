# tests/core/test_mention_integration.py
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from core.agent import AgentLoop
from core.cooldown import CooldownTracker
from core.events import EventBus
from core.executor import ToolExecutor
from core.fallback_messages import (
    COOLDOWN_ACTIVE_MESSAGE,
    RETRY_WAIT_MESSAGE,
    WORKING_MESSAGE,
)
from core.llm import (
    FunctionCallRequest,
    GenerationTurn,
    LlmContent,
    LlmPart,
    OnRetry,
    UsageInfo,
)
from core.mention import ChannelMessage, MentionEntry
from core.permissions import InMemoryTagProvider
from core.prompt import PromptAssembler
from core.registry import SkillRegistry
from core.registry_snapshot import build_snapshot
from core.resolvers import ResolverRegistry
from core.tool_types import Display, ToolContext
from services.common.notify import NotifyService
from tests.core.skill_factory import write_skill
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

    async def create_player(self):
        raise AssertionError("이 가짜는 이미 아는 플레이어(p1)만 다룬다 — _FakeNewPlayers를 써라")

    async def link_account(self, player_id: str, discord_id: str) -> None:
        raise AssertionError("이 가짜는 이미 아는 플레이어(p1)만 다룬다 — _FakeNewPlayers를 써라")


class _FakeNewPlayers:
    """C2 회귀: 처음 보는 discord_id("newbie")는 player_for_discord가 None을 돌려주고,
    MentionEntry.handle()이 create_player + link_account로 실제 플레이어를 만들어야 한다."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.linked: list[tuple[str, str]] = []

    async def player_for_discord(self, discord_id: str):
        return None

    async def create_player(self):
        from services.common.players import Player
        self.created.append("new-p1")
        return Player(
            id="new-p1", primary_discord_id=None, records_public=False, memos_public=False,
            goals_public=False, arcade_public=False, reviews_public=False, sync_report_public=True,
            first_seen_at="2026-01-01T00:00:00+00:00",
        )

    async def link_account(self, player_id: str, discord_id: str) -> None:
        self.linked.append((player_id, discord_id))

    async def accounts_for(self, player_id: str):
        from services.common.players import DiscordAccount
        return [DiscordAccount(discord_id="newbie", player_id=player_id, is_primary=True, linked_at="2026-01-01T00:00:00+00:00")]

    async def tags_for(self, player_id: str) -> set[str]:
        return set()


class _FakeMemory:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, player_id: str) -> str:
        return self.store.get(player_id, "")

    async def set(self, player_id: str, content: str) -> None:
        self.store[player_id] = content


class _FakeUsage:
    def __init__(self) -> None:
        self.recorded: list[dict[str, object]] = []

    async def month_total_usd(self, *, guild_id: str, year_month: str) -> float:
        return 0.0

    async def record(self, **kwargs) -> float:
        self.recorded.append(kwargs)
        return 0.0


class _CapExceededUsage(_FakeUsage):
    """회귀: 월 상한을 항상 넘긴 것으로 돌려준다 — guild 멘션이면 바로 막혀야 하고,
    DM 멘션이면 guild_id가 없어 이 검사 자체를 안 타야 한다."""

    async def month_total_usd(self, *, guild_id: str, year_month: str) -> float:
        return 999999.0


class _FakeSettings:
    async def get_value(self, *, scope, scope_id, skill, name, default):
        return default

    async def is_skill_enabled(self, *, guild_id, skill):
        return True


class _NoopTyping:
    async def __aenter__(self): return None
    async def __aexit__(self, *exc): return None


async def _ignore(text: str, displays: list[Display]) -> None:
    return None


def _registry(*extra_skill_dirs: Path) -> SkillRegistry:
    registry = SkillRegistry([Path("skills"), *extra_skill_dirs])
    registry.load()
    return registry


def _mention_entry(
    script,
    *,
    player_tags: set[str] | None = None,
    extra_skill_dirs: tuple[Path, ...] = (),
    agent_model: str = "gemini-3.8-flash",
    memory: _FakeMemory | None = None,
    event_bus: EventBus | None = None,
    players: Any | None = None,
    usage: _FakeUsage | None = None,
    registry_snapshot_enabled: bool = False,
) -> tuple[MentionEntry, _FakeUsage]:
    registry = _registry(*extra_skill_dirs)
    bus = event_bus if event_bus is not None else EventBus()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(), event_bus=bus,
    )
    prompt = PromptAssembler(rules_text=Path("prompts/rules.md").read_text(), persona_text=Path("prompts/persona.md").read_text(), registry=registry)
    snapshot = build_snapshot(registry) if registry_snapshot_enabled else None
    agent = AgentLoop(
        llm=FakeLlmAdapter(script), executor=executor, prompt=prompt, model=agent_model,
        thinking_level="low", max_tool_calls=5, max_tokens=50000, registry_snapshot=snapshot,
    )
    players_obj = players if players is not None else _FakePlayers()
    if player_tags is not None:
        players_obj.tags = player_tags
    usage_obj = usage if usage is not None else _FakeUsage()
    entry = MentionEntry(
        agent=agent, players=players_obj, notify=NotifyService.__new__(NotifyService), cooldown=CooldownTracker(),
        settings=_FakeSettings(), usage=usage_obj, bot_discord_id="bot", agent_model=agent_model,
        memory=memory if memory is not None else _FakeMemory(), event_bus=bus,
        known_skill_names={s.manifest.name for s in registry.enabled_skills()}, skill_loading_mode="instructions",
    )
    return entry, usage_obj


_DISPLAY_TOOL_BODY = (
    "from core.tool_types import Display\n\n\n"
    "@tool()\n"
    "async def show_border(ctx: ToolContext) -> ToolResult:\n"
    '    """테스트용: data에 숫자가 있고 optional 표시물도 있는 도구."""\n'
    "    return ToolResult(\n"
    "        data={\"borders\": {\"SSS+\": 7}},\n"
    "        display=Display(content=\"크로노 MAS 보더: SSS+ 7어택\"),\n"
    "        display_mode=\"optional\",\n"
    "    )\n"
)


@pytest.mark.asyncio
async def test_full_flow_open_skill_then_tool_call_and_display_shown_via_show_tag(tmp_path: Path) -> None:
    # chunithm-border_lookup은 ChartRef 해석기와 ctx.records가 있어야 실제로 성공하는데,
    # 이 통합 테스트의 ToolExecutor/ToolContext는 (다른 AgentLoop 테스트들처럼) 그 둘을
    # 비워 둔다 — 그 둘을 채우는 것은 이 결함(표시물이 send까지 안 간다) 수정과 무관한
    # 별개의 배선 범위다. 그래서 여기서는 인자 없이 바로 data+표시물을 돌려주는 테스트
    # 전용 도구를 하나 꽂아, "도구가 실제로 표시물을 냈을 때 그게 send까지 가는지"만 본다.
    skills_root = tmp_path / "skills"
    _ = write_skill(skills_root, "test-display", _DISPLAY_TOOL_BODY)

    call_lookup = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="test-display_show_border", args={})],
        usage=UsageInfo(input_tokens=100, output_tokens=5),
    )
    answer = GenerationTurn(
        # 숫자 규칙 위반은 더 이상 표시물을 강제로 다 보여주지 않는다(실기 2026-10-18: 목표
        # 하나 수정했는데 상관없는 목표 목록까지 새어 나왔다) — [[show:..]]로 명시해야 한다.
        content=LlmContent(role="model", parts=[LlmPart(text="보더는 7어택이다. [[show:d1]]")]),
        text="보더는 7어택이다. [[show:d1]]",
        usage=UsageInfo(input_tokens=50, output_tokens=5),
    )
    entry, usage = _mention_entry([call_lookup, answer], extra_skill_dirs=(skills_root,))
    sent: list[str] = []
    sent_displays: list[list[Display]] = []

    async def _collect(text: str, displays: list[Display]) -> None:
        sent.append(text)
        sent_displays.append(displays)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="크로노 마스 보더", mentions_bot=True)
    await entry.handle(
        trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC),
        send=_collect, typing=_NoopTyping(),
    )

    assert sent == ["보더는 7어택이다."]
    assert usage.recorded[0]["input_tokens"] == 150
    assert usage.recorded[0]["output_tokens"] == 10
    # 핵심 회귀 검증: [[show:..]]로 모델이 명시한 표시물이 실제로 send의 두 번째 인자까지
    # 도달해야 한다(core/mention.py가 계산만 하고 버리던 결함의 수정 대상).
    assert len(sent_displays) == 1
    [displays] = sent_displays
    assert len(displays) == 1
    assert displays[0].content is not None
    assert "크로노" in displays[0].content
    assert "보더" in displays[0].content


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

    async def _collect(text: str, displays: list[Display]) -> None:
        sent.append(text)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="관리 스킬 보여줘", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_collect, typing=_NoopTyping())

    assert relay_error is not None  # admin 태그 없는 플레이어 — "모르는 명령이에요" 류 오류가 모델에 전달됨
    assert sent == ["막혔다고 전달함"]


@pytest.mark.asyncio
async def test_full_flow_required_display_from_real_skill_tool_reaches_send() -> None:
    # C1 회귀: admin_skills(display_mode="required", 기본값)를 실제 멘션 경로(MentionEntry.handle
    # → AgentLoop.run → assemble_response → send)로 끝까지 불러서, 표시물이 실제로 send의
    # 두 번째 인자까지 도달하는지 본다. 이전 버그에서는 required 표시물을 담을 자리 자체가
    # 없어서 여기까지 올 방법이 없었다.
    call_admin = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="admin_skills", args={})],
        usage=UsageInfo(input_tokens=10, output_tokens=1),
    )
    answer = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="스킬 목록을 봤다.")]), text="스킬 목록을 봤다.")
    entry, _usage = _mention_entry(
        [call_admin, answer], player_tags={"admin"}, registry_snapshot_enabled=True,
    )
    sent: list[str] = []
    sent_displays: list[list[Display]] = []

    async def _collect(text: str, displays: list[Display]) -> None:
        sent.append(text)
        sent_displays.append(displays)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="관리 스킬 보여줘", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_collect, typing=_NoopTyping())

    assert sent == ["스킬 목록을 봤다."]
    assert len(sent_displays) == 1
    [displays] = sent_displays
    assert len(displays) == 1
    assert displays[0].content is not None
    assert "chunithm-border" in displays[0].content


@pytest.mark.asyncio
async def test_mention_entry_creates_player_for_unknown_discord_user() -> None:
    # C2 회귀: 처음 보는 discord_id로 멘션하면 예전에는 그냥 return해 아무 응답도 안 갔다.
    # 이제는 create_player+link_account로 플레이어를 만들고 정상 흐름(에이전트 호출,
    # 응답 전송)이 그대로 이어져야 한다.
    script = [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="반갑다")]), text="반갑다")]
    players = _FakeNewPlayers()
    entry, _usage = _mention_entry(script, players=players)

    sent: list[str] = []

    async def _collect(text: str, displays: list[Display]) -> None:
        sent.append(text)

    trigger = ChannelMessage(message_id="1", author_discord_id="newbie", is_bot=False, content="안녕", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_collect, typing=_NoopTyping())

    assert players.created == ["new-p1"]
    assert players.linked == [("new-p1", "newbie")]
    assert sent == ["반갑다"]  # 이전 버그에서는 응답이 전혀 안 갔다(그냥 return)


@pytest.mark.asyncio
async def test_mention_entry_emits_first_seen_event_for_new_player() -> None:
    # C2 재검토: core/slash.py의 _player_context 호출부(두 곳)는 is_new_player일 때
    # executor.event_bus.emit_notify("players.first_seen", {"player_id": ...})를 낸다.
    # MentionEntry.handle()도 같은 생명주기 이벤트를 같은 모양으로 내야 한다 — 안 그러면
    # 나중에 이 이벤트를 구독하는 스킬이 멘션으로 처음 온 플레이어만 조용히 놓친다.
    script = [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="반갑다")]), text="반갑다")]
    players = _FakeNewPlayers()
    bus = EventBus()
    received: list[dict[str, object]] = []

    async def _handler(ctx: ToolContext, payload: dict[str, object]) -> None:
        received.append(payload)

    bus.register_notify("players.first_seen", owner_skill="test-first-seen", fn=_handler, skill_services=[])
    entry, _usage = _mention_entry(script, players=players, event_bus=bus)

    trigger = ChannelMessage(message_id="1", author_discord_id="newbie", is_bot=False, content="안녕", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_ignore, typing=_NoopTyping())
    await bus.drain()

    assert received == [{"player_id": "new-p1"}]


@pytest.mark.asyncio
async def test_mention_entry_does_not_emit_first_seen_event_for_returning_player() -> None:
    # 위 테스트의 반대 축: 이미 아는 플레이어(p1)의 멘션에서는 players.first_seen이
    # 나가면 안 된다 — _FakePlayers.create_player/link_account는 불리면 바로 예외를
    # 던지므로, 이 테스트가 통과한다는 것 자체가 신규 플레이어 분기를 타지 않았다는 뜻이다.
    script = [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="또 왔네")]), text="또 왔네")]
    bus = EventBus()
    received: list[dict[str, object]] = []

    async def _handler(ctx: ToolContext, payload: dict[str, object]) -> None:
        received.append(payload)

    bus.register_notify("players.first_seen", owner_skill="test-first-seen", fn=_handler, skill_services=[])
    entry, _usage = _mention_entry(script, event_bus=bus)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="안녕", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_ignore, typing=_NoopTyping())
    await bus.drain()

    assert received == []


@pytest.mark.asyncio
async def test_mention_entry_records_usage_with_configured_agent_model_not_hardcoded() -> None:
    # 회귀: usage.record에 들어가는 model이 core/mention.py에 박아 둔 문자열이 아니라
    # MentionEntry에 실제로 설정된 agent_model을 그대로 써야 한다.
    script = [
        GenerationTurn(
            content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답",
            usage=UsageInfo(input_tokens=5, output_tokens=2),
        )
    ]
    entry, usage = _mention_entry(script, agent_model="gemini-9.9-custom")
    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="질문", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_ignore, typing=_NoopTyping())

    assert usage.recorded[0]["model"] == "gemini-9.9-custom"


@pytest.mark.asyncio
async def test_dm_mention_gets_cooldown_and_usage_but_skips_monthly_cap() -> None:
    # 회귀: DM(guild_id=None)도 쿨다운/사용량 기록은 받아야 하고, 월상한 검사만 건너뛴다.
    # _CapExceededUsage는 month_total_usd를 항상 터무니없이 큰 값으로 돌려준다 — guild
    # 멘션이었다면 바로 막혔을 상황인데, DM이라 guild_id가 없어 이 검사 자체를 안 탄다.
    script = [
        GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답1")]), text="답1"),
        GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답2")]), text="답2"),
    ]
    entry, usage = _mention_entry(script, usage=_CapExceededUsage())
    sent: list[str] = []

    async def _collect(text: str, displays: list[Display]) -> None:
        sent.append(text)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="질문", mentions_bot=True)
    now = datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC)
    await entry.handle(trigger=trigger, history=[trigger], guild_id=None, now=now, send=_collect, typing=_NoopTyping())
    await entry.handle(trigger=trigger, history=[trigger], guild_id=None, now=now, send=_collect, typing=_NoopTyping())

    assert sent[0] == "답1"  # 월상한이 999999여도 DM이라 상한 검사 자체를 안 타서 막히지 않는다
    assert COOLDOWN_ACTIVE_MESSAGE in sent[1]  # 두 번째는 DM도 쿨다운에 걸려야 한다
    assert len(usage.recorded) == 1  # 쿨다운에 막힌 두 번째는 기록되지 않는다
    assert usage.recorded[0]["guild_id"] == "dm"


@pytest.mark.asyncio
async def test_mention_entry_injects_player_memory_into_system_instruction() -> None:
    # 회귀: remember로 저장된 메모리가 다음 멘션의 system_instruction(prompt_context_snippets
    # 경유)에 실제로 들어가야 한다. 이전에는 저장만 되고 다시 읽히는 곳이 없었다.
    script = [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")]
    memory = _FakeMemory()
    memory.store["p1"] = "민트초코를 좋아한다"
    entry, _usage = _mention_entry(script, memory=memory)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="질문", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_ignore, typing=_NoopTyping())

    llm = cast("FakeLlmAdapter", entry._agent._llm)  # pyright: ignore[reportPrivateUsage]
    assert llm.calls
    system_instruction = llm.calls[0][1]
    assert "민트초코를 좋아한다" in system_instruction


_EMIT_TOOL_BODY = (
    "@tool()\n"
    "async def emit_test_event(ctx: ToolContext) -> ToolResult:\n"
    '    """테스트용: 이벤트를 하나 낸다."""\n'
    "    await ctx.emit(\"test.mention_event\", {\"player_id\": ctx.player_id})\n"
    "    return ToolResult(data={})\n"
)


@pytest.mark.asyncio
async def test_mention_tool_call_event_reaches_executors_shared_event_bus(tmp_path: Path) -> None:
    # M1 회귀: core/mention.py가 만드는 ToolContext는 executor와 같은 공유 EventBus를 써야
    # 한다. 이전에는 ToolContext가 기본값으로 매번 새 일회용 EventBus를 만들어, 도구가 낸
    # 이벤트가 executor에 등록된 진짜 핸들러에 닿지 않았다.
    skills_root = tmp_path / "skills"
    _ = write_skill(skills_root, "test-emit", _EMIT_TOOL_BODY)

    call_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="test-emit_emit_test_event", args={})],
    )
    answer_turn = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="냈다")]), text="냈다")

    bus = EventBus()
    received: list[dict[str, object]] = []

    async def _handler(ctx: ToolContext, payload: dict[str, object]) -> None:
        received.append(payload)

    bus.register_notify("test.mention_event", owner_skill="test-emit", fn=_handler, skill_services=[])

    entry, _usage = _mention_entry([call_turn, answer_turn], extra_skill_dirs=(skills_root,), event_bus=bus)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="이벤트 내줘", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_ignore, typing=_NoopTyping())

    for _ in range(5):  # emit_notify는 백그라운드 태스크로 돈다 — 돌 기회를 준다.
        await asyncio.sleep(0)

    assert received == [{"player_id": "p1"}]


class _RetryOnceLlmAdapter:
    """실제 어댑터의 재시도 루프를 흉내 내지 않고, on_retry를 한 번 부른 뒤 바로 최종 턴을
    돌려준다 — core/mention.py가 그 신호를 받아 RETRY_WAIT_MESSAGE를 보내는지만 본다."""

    def __init__(self, final_turn: GenerationTurn) -> None:
        self._final_turn = final_turn

    async def generate(
        self, *, model: str, system_instruction: str, contents, tools, thinking_level: str,
        on_retry: OnRetry | None = None,
    ) -> GenerationTurn:
        if on_retry is not None:
            await on_retry(1, 2.0)
        return self._final_turn


@pytest.mark.asyncio
async def test_mention_sends_retry_wait_message_once_before_final_answer() -> None:
    """2026-10-18: Gemini 503 재시도 중에는 "잠깐만 기다려 달라"를 한 번만 보내고, 재시도가
    끝나면 평소대로 최종 답을 보낸다."""
    registry = _registry()
    bus = EventBus()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(), event_bus=bus,
    )
    prompt = PromptAssembler(
        rules_text=Path("prompts/rules.md").read_text(), persona_text=Path("prompts/persona.md").read_text(),
        registry=registry,
    )
    answer_turn = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="다 됐다")]), text="다 됐다")
    agent = AgentLoop(
        llm=_RetryOnceLlmAdapter(answer_turn), executor=executor, prompt=prompt, model="gemini-3.8-flash",
        thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    entry = MentionEntry(
        agent=agent, players=_FakePlayers(), notify=NotifyService.__new__(NotifyService), cooldown=CooldownTracker(),
        settings=_FakeSettings(), usage=_FakeUsage(), bot_discord_id="bot", agent_model="gemini-3.8-flash",
        memory=_FakeMemory(), event_bus=bus, known_skill_names={s.manifest.name for s in registry.enabled_skills()},
        skill_loading_mode="instructions",
    )

    sent: list[str] = []

    async def _record_send(text: str, displays: list[Display]) -> None:
        del displays
        sent.append(text)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="질문", mentions_bot=True)
    await entry.handle(
        trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 18, tzinfo=UTC),
        send=_record_send, typing=_NoopTyping(),
    )

    assert sent == [RETRY_WAIT_MESSAGE, "다 됐다"]


class _SlowLlmAdapter:
    """generate가 정해 둔 시간만큼 멈췄다가 최종 턴을 돌려준다 — 느린 응답 안내 시험용."""

    def __init__(self, final_turn: GenerationTurn, delay: float) -> None:
        self._final_turn = final_turn
        self._delay = delay

    async def generate(
        self, *, model: str, system_instruction: str, contents, tools, thinking_level: str,
        on_retry: OnRetry | None = None,
    ) -> GenerationTurn:
        del on_retry
        await asyncio.sleep(self._delay)
        return self._final_turn


_HEAVY_TOOL_BODY = (
    "@tool()\n"
    "async def do_heavy(ctx: ToolContext) -> ToolResult:\n"
    '    """테스트용: heavy 스킬의 도구."""\n'
    "    return ToolResult(data={})\n"
)


@pytest.mark.asyncio
async def test_mention_sends_working_message_when_heavy_skill_tool_is_called(tmp_path: Path) -> None:
    """2026-10-18: "기다려 달라" 안내를 시간 기다렸다가(타이머) 보내던 걸 없애고, manifest에
    weight: heavy로 표시한 스킬의 도구를 부르는 순간 바로 보낸다."""
    skills_root = tmp_path / "skills"
    _ = write_skill(skills_root, "test-heavy", _HEAVY_TOOL_BODY, weight="heavy")

    call_heavy = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="test-heavy_do_heavy", args={})],
    )
    answer = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="다 됐다")]), text="다 됐다")
    entry, _usage = _mention_entry([call_heavy, answer], extra_skill_dirs=(skills_root,))

    sent: list[str] = []

    async def _record_send(text: str, displays: list[Display]) -> None:
        del displays
        sent.append(text)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="해 줘", mentions_bot=True)
    await entry.handle(
        trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 18, tzinfo=UTC),
        send=_record_send, typing=_NoopTyping(),
    )

    assert sent == [WORKING_MESSAGE, "다 됐다"]


@pytest.mark.asyncio
async def test_mention_does_not_send_working_message_for_light_only_slow_turn() -> None:
    """light 스킬(기본값)만 쓰는 턴은 LLM 응답이 느려도 "기다려 달라"를 보내지 않는다 —
    시간 기반 타이머를 없앤 뒤의 핵심 회귀(그 전엔 도구 2개 이상 부르는 평범한 턴에도
    거의 매번 떴다)."""
    registry = _registry()
    bus = EventBus()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(), event_bus=bus,
    )
    prompt = PromptAssembler(
        rules_text=Path("prompts/rules.md").read_text(), persona_text=Path("prompts/persona.md").read_text(),
        registry=registry,
    )
    answer_turn = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="다 됐다")]), text="다 됐다")
    agent = AgentLoop(
        llm=_SlowLlmAdapter(answer_turn, delay=0.05), executor=executor, prompt=prompt, model="gemini-3.8-flash",
        thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    entry = MentionEntry(
        agent=agent, players=_FakePlayers(), notify=NotifyService.__new__(NotifyService), cooldown=CooldownTracker(),
        settings=_FakeSettings(), usage=_FakeUsage(), bot_discord_id="bot", agent_model="gemini-3.8-flash",
        memory=_FakeMemory(), event_bus=bus, known_skill_names={s.manifest.name for s in registry.enabled_skills()},
        skill_loading_mode="instructions",
    )

    sent: list[str] = []

    async def _record_send(text: str, displays: list[Display]) -> None:
        del displays
        sent.append(text)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="질문", mentions_bot=True)
    await entry.handle(
        trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 18, tzinfo=UTC),
        send=_record_send, typing=_NoopTyping(),
    )

    assert sent == ["다 됐다"]
