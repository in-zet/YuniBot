# tests/core/test_mention_integration.py
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from core.agent import AgentLoop
from core.cooldown import CooldownTracker
from core.events import EventBus
from core.executor import ToolExecutor
from core.fallback_messages import (
    COOLDOWN_ACTIVE_MESSAGES,
    PRIVATE_DM_FAILED_MESSAGES,
    RETRY_WAIT_MESSAGES,
    WORKING_MESSAGES,
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
from core.tables import RowRef, TableBuilder, TableStore, cell
from core.threads import ThreadSession
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
        self.enqueued: list[dict[str, Any]] = []

    async def get(self, player_id: str) -> str:
        return self.store.get(player_id, "")

    async def set(self, player_id: str, content: str) -> None:
        self.store[player_id] = content

    async def enqueue(self, player_id: str, **kwargs: Any) -> bool:
        self.enqueued.append({"player_id": player_id, **kwargs})
        return True


class _FakeAddress:
    def __init__(self, name: str | None) -> None:
        self.name = name
        self.discord_names: list[str | None] = []

    async def resolve(self, player_id: str, *, discord_name: str | None = None) -> str | None:
        self.discord_names.append(discord_name)
        return self.name


class _FakeDmNotify:
    def __init__(self) -> None:
        self.dms: list[tuple[str, str]] = []

    async def dm(self, player_id: str, content: str, *, view: object | None = None) -> None:
        self.dms.append((player_id, content))


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
    address: _FakeAddress | None = None,
    notify: Any | None = None,
    conversation_skills: Any | None = None,
    threads: Any | None = None,
    tables: TableStore | None = None,
) -> tuple[MentionEntry, _FakeUsage]:
    registry = _registry(*extra_skill_dirs)
    bus = event_bus if event_bus is not None else EventBus()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(), event_bus=bus, tables=tables,
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
        agent=agent, players=players_obj, notify=notify if notify is not None else NotifyService.__new__(NotifyService),
        cooldown=CooldownTracker(),
        settings=_FakeSettings(), usage=usage_obj, bot_discord_id="bot", agent_model=agent_model,
        memory=memory if memory is not None else _FakeMemory(), event_bus=bus,
        known_skill_names={s.manifest.name for s in registry.enabled_skills()}, skill_loading_mode="instructions",
        address=address, conversation_skills=conversation_skills, threads=threads, tables=tables,
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
async def test_mention_entry_records_usage_with_fallback_model_when_it_answered() -> None:
    # 보조 모델이 답한 턴은 그 모델 요금으로 기록해야 한다(월 상한 계산이 실제와 맞도록).
    script = [
        GenerationTurn(
            content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답",
            usage=UsageInfo(input_tokens=5, output_tokens=2), model="gemini-3.5-flash-lite",
        )
    ]
    entry, usage = _mention_entry(script, agent_model="gemini-3.8-flash")
    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="질문", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_ignore, typing=_NoopTyping())

    assert usage.recorded[0]["model"] == "gemini-3.5-flash-lite"


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
    assert sent[1] in COOLDOWN_ACTIVE_MESSAGES  # 두 번째는 DM도 쿨다운에 걸려야 한다
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


@pytest.mark.asyncio
async def test_mention_entry_injects_resolved_address_with_the_authors_discord_name() -> None:
    script = [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")]
    address = _FakeAddress("디짐상")
    entry, _usage = _mention_entry(script, address=address)

    trigger = ChannelMessage(
        message_id="1", author_discord_id="p1", is_bot=False, content="질문", mentions_bot=True,
        author_display_name="서버닉",
    )
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_ignore, typing=_NoopTyping())

    llm = cast("FakeLlmAdapter", entry._agent._llm)  # pyright: ignore[reportPrivateUsage]
    assert "이 플레이어를 부를 이름: 디짐상" in llm.calls[0][1]
    assert address.discord_names == ["서버닉"]


@pytest.mark.asyncio
async def test_mention_entry_omits_address_line_when_unknown() -> None:
    script = [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")]
    entry, _usage = _mention_entry(script, address=_FakeAddress(None))
    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="질문", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_ignore, typing=_NoopTyping())

    llm = cast("FakeLlmAdapter", entry._agent._llm)  # pyright: ignore[reportPrivateUsage]
    assert "이 플레이어를 부를 이름:" not in llm.calls[0][1]


@pytest.mark.asyncio
async def test_mention_entry_queues_only_the_players_own_messages_for_summary() -> None:
    script = [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")]
    memory = _FakeMemory()
    entry, _usage = _mention_entry(script, memory=memory)

    earlier = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="나 왼손이 약해")
    bot_reply = ChannelMessage(message_id="2", author_discord_id="bot", is_bot=True, content="유니의 말")
    other = ChannelMessage(message_id="3", author_discord_id="p2", is_bot=False, content="쟤는 왼손 약해")
    trigger = ChannelMessage(
        message_id="4", author_discord_id="p1", is_bot=False, content="<@999> 호칭은 디짐으로 해 줘", mentions_bot=True,
        channel_id="c9",
    )
    now = datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC)
    await entry.handle(
        trigger=trigger, history=[earlier, bot_reply, other, trigger], guild_id="g1", now=now,
        send=_ignore, typing=_NoopTyping(),
    )

    [queued] = memory.enqueued
    assert queued["player_id"] == "p1"
    assert [(m.id, m.text) for m in queued["messages"]] == [("1", "나 왼손이 약해"), ("4", "호칭은 디짐으로 해 줘")]
    assert queued["guild_id"] == "g1" and queued["channel_id"] == "c9" and queued["message_id"] == "4"
    assert queued["now"] == now.timestamp()


@pytest.mark.asyncio
async def test_turn_that_used_an_admin_tool_is_not_queued_for_memory_summary() -> None:
    call_admin = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="admin_skills", args={})],
    )
    answer = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="봤다.")]), text="봤다.")
    memory = _FakeMemory()
    entry, _usage = _mention_entry(
        [call_admin, answer], player_tags={"admin"}, registry_snapshot_enabled=True, memory=memory,
    )

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="관리 스킬 보여줘", mentions_bot=True)
    await entry.handle(
        trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC),
        send=_ignore, typing=_NoopTyping(),
    )

    assert memory.enqueued == []


@pytest.mark.asyncio
async def test_mention_entry_survives_summary_queue_failure() -> None:
    class _BrokenMemory(_FakeMemory):
        async def enqueue(self, player_id: str, **kwargs: Any) -> bool:
            raise RuntimeError("db")

    script = [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")]
    entry, _usage = _mention_entry(script, memory=_BrokenMemory())
    sent: list[str] = []

    async def _collect(text: str, displays: list[Display]) -> None:
        sent.append(text)

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="질문", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_collect, typing=_NoopTyping())
    assert sent == ["답"]


_PRIVATE_TOOL_BODY = (
    "from core.tool_types import Display\n\n\n"
    "@tool()\n"
    "async def show_secret(ctx: ToolContext) -> ToolResult:\n"
    '    """테스트용: 본인에게만 보여야 하는 표시물을 낸다."""\n'
    "    return ToolResult(\n"
    "        data={},\n"
    "        display=Display(content=\"비밀 내용\", ephemeral=True, private=True),\n"
    "    )\n"
)


@pytest.mark.asyncio
async def test_private_display_goes_to_dm_instead_of_the_channel_reply(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _ = write_skill(skills_root, "test-private", _PRIVATE_TOOL_BODY)
    call = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="test-private_show_secret", args={})],
    )
    answer = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="DM으로 보냈어.")]), text="DM으로 보냈어.")
    notify = _FakeDmNotify()
    entry, _usage = _mention_entry([call, answer], extra_skill_dirs=(skills_root,), notify=notify)
    sent: list[tuple[str, list[Display]]] = []

    async def _collect(text: str, displays: list[Display]) -> None:
        sent.append((text, displays))

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="보여 줘", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_collect, typing=_NoopTyping())

    assert notify.dms == [("p1", "비밀 내용")]
    assert sent == [("DM으로 보냈어.", [])]


@pytest.mark.asyncio
async def test_private_display_tells_the_player_when_the_dm_cannot_be_sent(tmp_path: Path) -> None:
    class _BrokenDm:
        async def dm(self, player_id: str, content: str, *, view: object | None = None) -> None:
            raise RuntimeError("Cannot send messages to this user")

    skills_root = tmp_path / "skills"
    _ = write_skill(skills_root, "test-private", _PRIVATE_TOOL_BODY)
    call = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="test-private_show_secret", args={})],
    )
    answer = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="DM으로 보냈어.")]), text="DM으로 보냈어.")
    entry, _usage = _mention_entry([call, answer], extra_skill_dirs=(skills_root,), notify=_BrokenDm())
    sent: list[tuple[str, list[Display]]] = []

    async def _collect(text: str, displays: list[Display]) -> None:
        sent.append((text, displays))

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="보여 줘", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC), send=_collect, typing=_NoopTyping())

    [(text, displays)] = sent
    assert any(m in text for m in PRIVATE_DM_FAILED_MESSAGES) and displays == []


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
    돌려준다 — core/mention.py가 그 신호를 받아 RETRY_WAIT_MESSAGES 중 하나를 보내는지만 본다."""

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

    assert len(sent) == 2 and sent[0] in RETRY_WAIT_MESSAGES and sent[1] == "다 됐다"


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

    assert len(sent) == 2 and sent[0] in WORKING_MESSAGES and sent[1] == "다 됐다"


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


class _FakeSkillStore:
    def __init__(self) -> None:
        self.saved: dict[str, list[str]] = {}

    async def save(self, message_id: str, skills: list[str], *, now: float) -> None:
        if skills:
            self.saved[message_id] = skills

    async def get(self, message_id: str) -> list[str]:
        return self.saved.get(message_id, [])


def _open_skill_turns(skill: str) -> list[GenerationTurn]:
    call = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="open_skill", args={"skill_name": skill})],
    )
    return [call, GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")]


def _sender(reply_id: str, sent: list[str]):
    async def _send(text: str, displays: list[Display]) -> str | None:
        sent.append(text)
        return reply_id

    return _send


_NOW = datetime(2026, 10, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_skills_opened_in_a_reply_are_carried_to_the_next_reply_in_the_chain() -> None:
    store = _FakeSkillStore()
    plain = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답2")]), text="답2")
    entry, _usage = _mention_entry([*_open_skill_turns("chunithm-border"), plain], conversation_skills=store)

    first = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="보더", mentions_bot=True)
    await entry.handle(trigger=first, history=[first], guild_id="g1", now=_NOW, send=_sender("b1", []), typing=_NoopTyping())
    assert store.saved == {"b1": ["chunithm-border"]}

    bot_reply = ChannelMessage(message_id="b1", author_discord_id="bot", is_bot=True, content="답")
    second = ChannelMessage(
        message_id="2", author_discord_id="p1", is_bot=False, content="그럼 이건?", mentions_bot=True, reference_id="b1",
    )
    await entry.handle(
        trigger=second, history=[first, bot_reply, second], guild_id="g1", now=_NOW + timedelta(minutes=1),
        send=_sender("b2", []), typing=_NoopTyping(),
    )

    # 두 번째 답에서는 open_skill을 부르지 않았는데도 펼친 채로 이어받는다.
    assert store.saved["b2"] == ["chunithm-border"]


@pytest.mark.asyncio
async def test_a_fresh_mention_does_not_inherit_opened_skills() -> None:
    store = _FakeSkillStore()
    store.saved["b1"] = ["chunithm-border"]
    plain = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")
    entry, _usage = _mention_entry([plain], conversation_skills=store)

    trigger = ChannelMessage(message_id="2", author_discord_id="p1", is_bot=False, content="안녕", mentions_bot=True)
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=_NOW, send=_sender("b2", []), typing=_NoopTyping())

    assert "b2" not in store.saved


@pytest.mark.asyncio
async def test_store_failure_does_not_break_the_reply() -> None:
    class _BrokenStore(_FakeSkillStore):
        async def save(self, message_id: str, skills: list[str], *, now: float) -> None:
            raise RuntimeError("db")

        async def get(self, message_id: str) -> list[str]:
            raise RuntimeError("db")

    entry, _usage = _mention_entry(_open_skill_turns("chunithm-border"), conversation_skills=_BrokenStore())
    sent: list[str] = []
    trigger = ChannelMessage(
        message_id="2", author_discord_id="p1", is_bot=False, content="안녕", mentions_bot=True, reference_id="b1",
    )
    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=_NOW, send=_sender("b2", sent), typing=_NoopTyping())

    assert sent == ["답"]


def _thread_session(*, player_id: str = "p1") -> ThreadSession:
    class _Transport:
        async def send_message(self, text: str) -> None:
            pass

    return ThreadSession(
        thread_id="t1", skill_name="chunithm-border", player_id=player_id, transport=_Transport(),
        channel_id="c1", idle_timeout=300.0, close_timeout=300.0, on_close=lambda t: None,
    )


@pytest.mark.asyncio
async def test_thread_message_is_answered_without_a_mention_and_keeps_the_threads_skills() -> None:
    plain = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")
    store = _FakeSkillStore()
    memory = _FakeMemory()
    entry, _usage = _mention_entry([plain], conversation_skills=store, memory=memory)
    thread = _thread_session()
    sent: list[str] = []

    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="오늘 어땠냐면", channel_id="c1")
    await entry.handle(
        trigger=trigger, history=[trigger], guild_id="g1", now=_NOW, send=_sender("b1", sent), typing=_NoopTyping(),
        thread=thread,
    )

    assert sent == ["답"]
    assert thread.opened_skills == ["chunithm-border"]
    # 스레드 대화는 닫힐 때 한 번에 요약되므로 답장마다 대기 목록에 쌓지 않고, 답장 사슬 저장소도 쓰지 않는다.
    assert memory.enqueued == []
    assert store.saved == {}


@pytest.mark.asyncio
async def test_thread_message_from_another_player_is_ignored() -> None:
    entry, _usage = _mention_entry([])
    sent: list[str] = []
    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="안녕", channel_id="c1")

    await entry.handle(
        trigger=trigger, history=[trigger], guild_id="g1", now=_NOW, send=_sender("b1", sent), typing=_NoopTyping(),
        thread=_thread_session(player_id="someone-else"),
    )

    assert sent == []


@pytest.mark.asyncio
async def test_message_without_mention_or_thread_is_ignored() -> None:
    entry, _usage = _mention_entry([])
    sent: list[str] = []
    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="안녕", channel_id="c1")

    await entry.handle(trigger=trigger, history=[trigger], guild_id="g1", now=_NOW, send=_sender("b1", sent), typing=_NoopTyping())

    assert sent == []


def _tool_names(entry: MentionEntry) -> list[set[str]]:
    llm = cast("Any", entry._agent._llm)
    return [{d.name for d in tools} for _m, _s, _c, tools in llm.calls]


@pytest.mark.asyncio
async def test_thread_guidance_goes_into_the_prompt_only_inside_the_thread() -> None:
    plain = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")
    entry, _usage = _mention_entry([plain])
    thread = _thread_session()
    thread.guidance = "[후기 대화] 한두 문장으로만 말한다"
    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="좋았어", channel_id="c1")
    await entry.handle(
        trigger=trigger, history=[trigger], guild_id=None, now=_NOW, send=_sender("b1", []), typing=_NoopTyping(),
        thread=thread,
    )
    system_instruction = cast("Any", entry._agent._llm).calls[0][1]
    assert "[후기 대화] 한두 문장으로만 말한다" in system_instruction

    mention_entry, _usage = _mention_entry([plain])
    mention = ChannelMessage(message_id="2", author_discord_id="p1", is_bot=False, content="안녕", mentions_bot=True)
    await mention_entry.handle(
        trigger=mention, history=[mention], guild_id=None, now=_NOW, send=_sender("b2", []), typing=_NoopTyping()
    )
    assert "[후기 대화]" not in cast("Any", mention_entry._agent._llm).calls[0][1]


@pytest.mark.asyncio
async def test_end_conversation_tool_is_offered_only_inside_a_thread() -> None:
    plain = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")
    in_thread, _usage = _mention_entry([plain])
    trigger = ChannelMessage(message_id="1", author_discord_id="p1", is_bot=False, content="안녕", channel_id="c1")
    await in_thread.handle(
        trigger=trigger, history=[trigger], guild_id=None, now=_NOW, send=_sender("b1", []), typing=_NoopTyping(),
        thread=_thread_session(),
    )
    outside, _usage = _mention_entry([plain])
    mention = ChannelMessage(message_id="2", author_discord_id="p1", is_bot=False, content="안녕", mentions_bot=True)
    await outside.handle(
        trigger=mention, history=[mention], guild_id=None, now=_NOW, send=_sender("b2", []), typing=_NoopTyping()
    )

    assert "end_conversation" in _tool_names(in_thread)[0]
    assert "end_conversation" not in _tool_names(outside)[0]


@pytest.mark.asyncio
async def test_end_conversation_closes_the_thread_after_the_farewell_and_keeps_the_transcript() -> None:
    from core.threads import ThreadManager, ThreadTranscript

    closed: list[ThreadTranscript] = []
    manager = ThreadManager()

    class _Transport:
        async def send_message(self, text: str) -> None:
            pass

    session = await manager.open(
        skill_name="chunithm-border", player_id="p1", transport=_Transport(), channel_id="c1",
        on_close=closed.append, now=_NOW,
    )
    end_call = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="end_conversation", args={})],
    )
    farewell = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="수고했어!")]), text="수고했어!")
    entry, _usage = _mention_entry([end_call, farewell], threads=manager)
    sent: list[str] = []

    await manager.receive(session.thread_id, "p1", "이만 갈게", now=_NOW, message_id="m1")
    trigger = ChannelMessage(message_id="m1", author_discord_id="p1", is_bot=False, content="이만 갈게", channel_id="c1")
    await entry.handle(
        trigger=trigger, history=[trigger], guild_id=None, now=_NOW, send=_sender("b1", sent), typing=_NoopTyping(),
        thread=session,
    )

    assert sent == ["수고했어!"]
    assert len(closed) == 1
    assert closed[0].closed_reason == "explicit"
    assert [(m.content, m.is_yuni) for m in closed[0].messages] == [("이만 갈게", False), ("수고했어!", True)]


@pytest.mark.asyncio
async def test_thread_stays_open_when_the_model_does_not_end_it() -> None:
    from core.threads import ThreadManager

    manager = ThreadManager()

    class _Transport:
        async def send_message(self, text: str) -> None:
            pass

    session = await manager.open(
        skill_name="chunithm-border", player_id="p1", transport=_Transport(), channel_id="c1",
        on_close=lambda t: None, now=_NOW,
    )
    plain = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="그랬구나")]), text="그랬구나")
    entry, _usage = _mention_entry([plain], threads=manager)
    trigger = ChannelMessage(message_id="m1", author_discord_id="p1", is_bot=False, content="힘들었어", channel_id="c1")

    await entry.handle(
        trigger=trigger, history=[trigger], guild_id=None, now=_NOW, send=_sender("b1", []), typing=_NoopTyping(),
        thread=session,
    )

    assert manager.session_for_channel("c1") is session


# ---- 작업 표 수명 ----

_TABLE_TOOL_BODY = (
    "from core.tables import RowRef, TableBuilder, cell\n\n\n"
    "@tool()\n"
    "async def make_table(ctx: ToolContext) -> ToolResult:\n"
    '    """테스트용: 표를 만든다."""\n'
    '    builder = TableBuilder(subject="p1", unit="채보").column("song", "곡", bare=True).step("테스트 표")\n'
    '    builder.row(RowRef("chunithm.chart", "1"), {"song": cell("크로노")})\n'
    "    return ToolResult(table=builder.build())\n"
)


def _call(name: str, call_id: str = "1") -> GenerationTurn:
    return GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id=call_id, name=name, args={})],
    )


def _say(text: str) -> GenerationTurn:
    return GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text=text)]), text=text)


def _trigger(message_id: str, *, reference_id: str | None = None, channel_id: str | None = "c1") -> ChannelMessage:
    return ChannelMessage(
        message_id=message_id, author_discord_id="p1", is_bot=False, content="표 보여 줘", mentions_bot=True,
        reference_id=reference_id, channel_id=channel_id,
    )


def _yuni(message_id: str) -> ChannelMessage:
    return ChannelMessage(message_id=message_id, author_discord_id="bot", is_bot=True, content="표야", channel_id="c1")


async def _run(entry: MentionEntry, trigger: ChannelMessage, history: list[ChannelMessage], *, sent_id: str = "m100") -> list[list[Display]]:
    shown: list[list[Display]] = []

    async def _send(text: str, displays: list[Display]) -> str | None:
        shown.append(displays)
        return sent_id

    await entry.handle(
        trigger=trigger, history=history, guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC),
        send=_send, typing=_NoopTyping(),
    )
    return shown


def _system_prompt(script: FakeLlmAdapter) -> str:
    return script.calls[-1][1]


@pytest.mark.asyncio
async def test_only_the_last_table_of_a_turn_is_kept_and_bound_to_the_reply(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _ = write_skill(skills_root, "test-tables", _TABLE_TOOL_BODY)
    store = TableStore()
    turns = [_call("test-tables_make_table", "1"), _call("test-tables_make_table", "2"), _say("두 번 만들었어")]
    entry, _usage = _mention_entry(turns, extra_skill_dirs=(skills_root,), tables=store)
    trigger = _trigger("1")

    shown = await _run(entry, trigger, [trigger])

    assert store.ids("p1", "c1") == ["t2"]  # 중간 표 t1은 버린다
    assert store.table_for_message("p1", "c1", "m100") == "t2"
    assert store.last_bound("p1", "c1") == "t2"
    assert [d.content for d in shown[0]] == ["테스트 표\n1. 크로노"]  # 마지막 표만 그린다


@pytest.mark.asyncio
async def test_no_table_is_stored_when_there_is_no_channel(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _ = write_skill(skills_root, "test-tables", _TABLE_TOOL_BODY)
    store = TableStore()
    entry, _usage = _mention_entry(
        [_call("test-tables_make_table"), _say("끝")], extra_skill_dirs=(skills_root,), tables=store
    )
    trigger = _trigger("1", channel_id=None)

    await _run(entry, trigger, [trigger])

    assert store.ids("p1", "c1") == []


def _store_with_bound_table(*, stale: bool = False) -> TableStore:
    store = TableStore()
    builder = TableBuilder(subject="p1", unit="채보").column("song", "곡").column("score", "점수", "number")
    builder.column("id", "아이디", slash_only=True)
    builder.row(RowRef("k", "1"), {"song": cell("A"), "score": cell(1), "id": cell("g1")})
    builder.steps(["15.0 상수 MAS", "내 점수"])
    _ = store.put("p1", "c1", builder.build(), now=0, stale=stale)
    store.bind("p1", "c1", "t1", "m100")
    return store


@pytest.mark.asyncio
async def test_alive_tables_and_number_base_lines_go_into_the_prompt() -> None:
    store = _store_with_bound_table()
    llm_turns = [_say("응")]
    entry, _usage = _mention_entry(llm_turns, tables=store)
    llm = entry._agent._llm  # pyright: ignore[reportPrivateUsage]
    trigger = _trigger("2", reference_id="m100")

    await _run(entry, trigger, [_yuni("m100"), trigger])

    prompt = _system_prompt(llm)  # pyright: ignore[reportArgumentType]
    assert "[살아 있는 표] t1 — 15.0 상수 MAS → 내 점수 (1채보, 칸: 곡·점수)" in prompt
    assert "아이디" not in prompt  # slash_only 칸은 알리지 않는다
    assert "답장한 메시지의 표: t1" in prompt
    assert "마지막으로 보여 준 표: t1" in prompt


@pytest.mark.asyncio
async def test_not_a_reply_has_no_replied_line_but_still_the_last_shown_table() -> None:
    store = _store_with_bound_table()
    entry, _usage = _mention_entry([_say("응")], tables=store)
    trigger = _trigger("2")

    await _run(entry, trigger, [_yuni("m100"), trigger])

    prompt = _system_prompt(entry._agent._llm)  # pyright: ignore[reportPrivateUsage,reportArgumentType]
    assert "답장한 메시지의 표: t" not in prompt
    assert "마지막으로 보여 준 표: t1" in prompt


@pytest.mark.asyncio
async def test_stale_table_is_flagged_in_the_prompt() -> None:
    store = _store_with_bound_table(stale=True)
    entry, _usage = _mention_entry([_say("응")], tables=store)
    trigger = _trigger("2")

    await _run(entry, trigger, [_yuni("m100"), trigger])

    assert "[살아 있는 표] t1 (동기화 전 값) —" in _system_prompt(entry._agent._llm)  # pyright: ignore[reportPrivateUsage,reportArgumentType]


@pytest.mark.asyncio
async def test_table_pushed_out_of_the_context_is_deleted() -> None:
    store = _store_with_bound_table()
    entry, _usage = _mention_entry([_say("응")], tables=store)
    # 유니의 m100 뒤로 플레이어 메시지가 10개를 넘으면 m100은 맥락 밖이다
    later = [
        ChannelMessage(message_id=f"p{i}", author_discord_id="p1", is_bot=False, content="잡담", channel_id="c1")
        for i in range(11)
    ]
    trigger = _trigger("2")

    await _run(entry, trigger, [_yuni("m100"), *later, trigger])

    assert store.ids("p1", "c1") == []
    assert "[살아 있는 표] t" not in _system_prompt(entry._agent._llm)  # pyright: ignore[reportPrivateUsage,reportArgumentType]


@pytest.mark.asyncio
async def test_other_players_and_channels_tables_are_left_alone() -> None:
    store = _store_with_bound_table()
    _ = store.put("p2", "c1", TableBuilder().column("a").build(), now=0)
    _ = store.put("p1", "c2", TableBuilder().column("a").build(), now=0)
    entry, _usage = _mention_entry([_say("응")], tables=store)
    trigger = _trigger("2")

    await _run(entry, trigger, [trigger])  # 맥락에 m100이 없다

    assert store.ids("p1", "c1") == []
    assert store.ids("p2", "c1") == ["t1"] and store.ids("p1", "c2") == ["t1"]


@pytest.mark.asyncio
async def test_thread_keeps_its_tables_until_the_thread_closes() -> None:
    store = _store_with_bound_table()
    entry, _usage = _mention_entry([_say("응")], tables=store)
    trigger = _trigger("2")

    snippets = entry._table_snippets(  # pyright: ignore[reportPrivateUsage]
        player_id="p1", trigger=trigger, context=[trigger], thread=cast("ThreadSession", object())
    )

    assert store.ids("p1", "c1") == ["t1"]  # 맥락에 m100이 없어도 지우지 않는다
    assert snippets[0].startswith("[살아 있는 표] t1")
