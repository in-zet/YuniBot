from __future__ import annotations

from pathlib import Path

import pytest

from core.agent import AgentLoop
from core.executor import ToolExecutor
from core.llm import (
    FunctionCallRequest,
    GenerationTurn,
    LlmContent,
    LlmPart,
    ToolDeclaration,
    UsageInfo,
)
from core.permissions import InMemoryTagProvider
from core.prompt import PromptAssembler
from core.registry import SkillRegistry
from core.resolvers import ResolverRegistry
from core.skill_loading import Mode, SkillLoadState
from core.tool_types import ToolContext
from tests.support.fake_llm import FakeLlmAdapter


def _registry() -> SkillRegistry:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    return registry


def _executor(registry: SkillRegistry) -> ToolExecutor:
    return ToolExecutor(
        resolvers=ResolverRegistry(),
        tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(),
    )


def _ctx() -> ToolContext:
    return ToolContext(player_id="p1", tags=frozenset(), services={})


def _state(registry: SkillRegistry, mode: Mode = "instructions") -> SkillLoadState:
    return SkillLoadState(mode=mode, known_skills={s.manifest.name for s in registry.enabled_skills()})


@pytest.mark.asyncio
async def test_agent_loop_returns_text_when_model_answers_directly() -> None:
    registry = _registry()
    script = [
        GenerationTurn(
            content=LlmContent(role="model", parts=[LlmPart(text="직접 답했다")]),
            text="직접 답했다",
            usage=UsageInfo(input_tokens=100, output_tokens=10),
        )
    ]
    loop = AgentLoop(
        llm=FakeLlmAdapter(script), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    result = await loop.run(ctx=_ctx(), skill_state=_state(registry), user_text="안녕", prompt_context_snippets=[])
    assert result.text == "직접 답했다"
    assert result.hit_tool_call_cap is False


@pytest.mark.asyncio
async def test_agent_loop_calls_core_tool_then_answers() -> None:
    registry = _registry()
    call_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="open_skill", args={"skill_name": "chunithm-border"})],
        usage=UsageInfo(input_tokens=50, output_tokens=5),
    )
    answer_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[LlmPart(text="펼쳤다")]),
        text="펼쳤다",
        usage=UsageInfo(input_tokens=60, output_tokens=5),
    )
    loop = AgentLoop(
        llm=FakeLlmAdapter([call_turn, answer_turn]), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    state = _state(registry, mode="full")
    result = await loop.run(ctx=_ctx(), skill_state=state, user_text="보더 스킬 써도 돼?", prompt_context_snippets=[])
    assert result.text == "펼쳤다"
    assert state.level_for("chunithm-border") == "full"
    assert result.usage.input_tokens == 110
    assert result.usage.output_tokens == 10


@pytest.mark.asyncio
async def test_agent_loop_stops_after_max_tool_calls_without_infinite_loop() -> None:
    registry = _registry()

    def always_call(*_args: object) -> GenerationTurn:
        return GenerationTurn(
            content=LlmContent(role="model", parts=[]),
            function_calls=[FunctionCallRequest(call_id="x", name="open_skill", args={"skill_name": "chunithm-border"})],
            usage=UsageInfo(input_tokens=10, output_tokens=1),
        )

    loop = AgentLoop(
        llm=FakeLlmAdapter(always_call), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=3, max_tokens=50000,
    )
    result = await loop.run(ctx=_ctx(), skill_state=_state(registry, mode="full"), user_text="질문", prompt_context_snippets=[])
    assert result.hit_tool_call_cap is True
    assert result.text is None  # 모델이 끝까지 텍스트를 안 줬다 — mention 쪼음(Task 11)이 고정 문구로 바꾼다


@pytest.mark.asyncio
async def test_agent_loop_surfaces_tag_error_from_executor_as_function_response() -> None:
    registry = _registry()
    call_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(
            call_id="1", name="chunithm-border_remaining",
            args={"chart": "크로노 MAS", "rank": "SSS+"},
        )],
        usage=UsageInfo(input_tokens=10, output_tokens=1),
    )
    seen_function_responses: list[dict[str, object]] = []

    def script(
        model: str, system: str, contents: list[LlmContent], tools: list[ToolDeclaration]
    ) -> GenerationTurn:
        if len(contents) == 1:
            return call_turn
        last_content = contents[-1]
        for part in last_content.parts:
            if part.function_response is not None:
                seen_function_responses.append(part.function_response.response)
        return GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="막혔다")]), text="막혔다")

    loop = AgentLoop(
        llm=FakeLlmAdapter(script), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    # chunithm-synced 태그가 없는 플레이어 — chunithm-border_remaining은 tags=["chunithm-synced"]
    result = await loop.run(ctx=_ctx(), skill_state=_state(registry, mode="full"), user_text="질문", prompt_context_snippets=[])
    assert result.text == "막혔다"
    assert "error" in seen_function_responses[0]


@pytest.mark.asyncio
async def test_agent_loop_rejects_confirm_always_tool_without_crashing() -> None:
    registry = _registry()
    call_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(
            call_id="1", name="admin_set_limits",
            args={"monthly_cost_cap": 20.0, "player_cooldown_seconds": 5},
        )],
        usage=UsageInfo(input_tokens=10, output_tokens=1),
    )
    seen_function_responses: list[dict[str, object]] = []

    def script(
        model: str, system: str, contents: list[LlmContent], tools: list[ToolDeclaration]
    ) -> GenerationTurn:
        if len(contents) == 1:
            return call_turn
        last_content = contents[-1]
        for part in last_content.parts:
            if part.function_response is not None:
                seen_function_responses.append(part.function_response.response)
        return GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="알겠다")]), text="알겠다")

    loop = AgentLoop(
        llm=FakeLlmAdapter(script), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    # admin 스킬은 tags=[admin]을 요구한다. admin_set_limits는 confirm="always"라
    # ToolExecutor.invoke가 result=None, needs_confirm=True를 돌려주는 경로를 탄다 —
    # AgentLoop._invoke의 assert outcome.result is not None 바로 앞 분기다.
    ctx = ToolContext(player_id="p1", tags=frozenset({"admin"}), services={})
    result = await loop.run(ctx=ctx, skill_state=_state(registry, mode="full"), user_text="월 비용 상한 20불로 바꿔줘", prompt_context_snippets=[])
    assert result.text == "알겠다"
    error_value = seen_function_responses[0].get("error")
    assert isinstance(error_value, str)
    assert "슬래시 명령으로만 확인할 수 있다" in error_value
