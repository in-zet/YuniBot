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
from core.tool_types import Display
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


def _registry(*extra_skill_dirs: Path) -> SkillRegistry:
    registry = SkillRegistry([Path("skills"), *extra_skill_dirs])
    registry.load()
    return registry


def _mention_entry(
    script, *, player_tags: set[str] | None = None, extra_skill_dirs: tuple[Path, ...] = ()
) -> tuple[MentionEntry, _FakeUsage]:
    registry = _registry(*extra_skill_dirs)
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
async def test_full_flow_open_skill_then_tool_call_and_display_shown_on_numeric_violation(tmp_path: Path) -> None:
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
    bad_answer = GenerationTurn(
        content=LlmContent(role="model", parts=[LlmPart(text="12345어택이다.")]),  # data에 없는 숫자
        text="12345어택이다.",
        usage=UsageInfo(input_tokens=50, output_tokens=5),
    )
    entry, usage = _mention_entry([call_lookup, bad_answer], extra_skill_dirs=(skills_root,))
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

    assert sent == ["12345어택이다."]
    assert usage.recorded[0]["input_tokens"] == 150
    assert usage.recorded[0]["output_tokens"] == 10
    # 핵심 회귀 검증: 숫자 규칙 위반(모델이 data에 없는 "12345어택"을 말함) 시
    # assemble_response가 강제로 모은 표시물이 실제로 send의 두 번째 인자까지 도달해야 한다
    # (core/mention.py가 계산만 하고 버리던 결함의 수정 대상).
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
