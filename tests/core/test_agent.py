from __future__ import annotations

from pathlib import Path
from typing import Any

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
from core.registry_snapshot import build_snapshot
from core.resolvers import ResolverRegistry
from core.skill_loading import Mode, SkillLoadState
from core.tool_types import Display, ToolContext, ToolResult
from services.common.memory import RememberResult
from tests.core.skill_factory import write_skill
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
async def test_agent_loop_reports_served_model_only_when_fallback_answered() -> None:
    registry = _registry()

    async def _run(turn_model: str | None) -> str | None:
        script = [GenerationTurn(
            content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답", model=turn_model,
        )]
        loop = AgentLoop(
            llm=FakeLlmAdapter(script), executor=_executor(registry),
            prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
            model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
        )
        result = await loop.run(ctx=_ctx(), skill_state=_state(registry), user_text="안녕", prompt_context_snippets=[])
        return result.served_model

    assert await _run(None) is None
    assert await _run("gemini-3.5-flash-lite") == "gemini-3.5-flash-lite"


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
    assert result.text is None  # 모델이 끝까지 텍스트를 안 줬다 — core/mention.py가 고정 문구로 바꾼다


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


_CAPPED_TOOL_BODY = (
    "@tool(max_calls_per_turn=1)\n"
    "async def do_search(ctx: ToolContext) -> ToolResult:\n"
    '    """테스트용: 응답당 호출 상한이 있는 도구."""\n'
    "    return ToolResult(data={\"ok\": True})\n"
)


@pytest.mark.asyncio
async def test_agent_loop_rejects_call_beyond_per_tool_cap_but_keeps_turn_going(tmp_path: Path) -> None:
    """max_calls_per_turn은 전역 max_tool_calls_per_turn과 달리 이 도구 하나만 거절한다 —
    턴 전체는 멈추지 않고, 모델이 거절 사유를 보고 답을 마무리할 수 있다(10단계 Part 1)."""
    skills_root = tmp_path / "skills"
    write_skill(skills_root, "capped-search", _CAPPED_TOOL_BODY)
    registry = SkillRegistry([Path("skills"), skills_root])
    registry.load()
    assert "capped-search" in {s.manifest.name for s in registry.enabled_skills()}, registry.failures()

    first_call = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="capped-search_do_search", args={})],
    )
    second_call = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="2", name="capped-search_do_search", args={})],
    )
    seen_function_responses: list[dict[str, object]] = []

    def script(
        model: str, system: str, contents: list[LlmContent], tools: list[ToolDeclaration]
    ) -> GenerationTurn:
        for part in contents[-1].parts:
            if part.function_response is not None:
                seen_function_responses.append(part.function_response.response)
        if len(seen_function_responses) == 0:
            return first_call
        if len(seen_function_responses) == 1:
            return second_call
        return GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="끝냈다")]), text="끝냈다")

    loop = AgentLoop(
        llm=FakeLlmAdapter(script), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    result = await loop.run(
        ctx=_ctx(), skill_state=_state(registry, mode="full"), user_text="검색해줘", prompt_context_snippets=[]
    )

    assert result.text == "끝냈다"
    assert result.hit_tool_call_cap is False
    assert "error" not in seen_function_responses[0]
    assert "error" in seen_function_responses[1]


@pytest.mark.asyncio
async def test_agent_loop_auto_confirms_confirm_always_tool_without_a_button() -> None:
    """멘션 경로에는 확인 버튼을 낼 인터랙션이 없어, confirm="always"/"bulk" 도구는 거부하지
    않고 바로 실행한다(2026-10-18 사용자 결정 — 실기에서 "일괄 삭제는 슬래시로만" 거절이
    불편하다는 피드백)."""
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

    class _FakeSettings:
        def __init__(self) -> None:
            self.saved: dict[str, object] = {}

        async def set_value(self, *, scope: str, scope_id: str, skill: str, name: str, value: object, updated_by: str) -> None:
            self.saved[name] = value

    settings = _FakeSettings()
    # 스킬 도구는 ctx.services가 아니라 executor가 쥔 전역 services에서 manifest가
    # 선언한 것만 골라 받는다(AgentLoop._scoped_ctx) — 그래서 settings를 executor에 넣는다.
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(), services={"settings": settings},
    )
    loop = AgentLoop(
        llm=FakeLlmAdapter(script), executor=executor,
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    # admin 스킬은 tags=[admin]을 요구한다. admin_set_limits는 confirm="always"라
    # ToolExecutor.invoke가 result=None, needs_confirm=True를 돌려주는 경로를 타지만,
    # AgentLoop._invoke가 이제 그 token으로 바로 executor.confirm(confirmed=True)을 부른다.
    ctx = ToolContext(player_id="p1", tags=frozenset({"admin"}), services={}, guild_id="g1")
    result = await loop.run(ctx=ctx, skill_state=_state(registry, mode="full"), user_text="월 비용 상한 20불로 바꿔줘", prompt_context_snippets=[])
    assert result.text == "알겠다"
    assert "error" not in seen_function_responses[0]
    assert seen_function_responses[0] == {"monthly_cost_cap": 20.0, "player_cooldown_seconds": 5}
    assert settings.saved["월 비용 상한"] == 20.0
    assert settings.saved["플레이어 쿨다운"] == 5


@pytest.mark.asyncio
async def test_agent_loop_handles_list_capabilities_directly() -> None:
    registry = _registry()
    call_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="list_capabilities", args={})],
    )
    answer_turn = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="안내함")]), text="안내함")
    loop = AgentLoop(
        llm=FakeLlmAdapter([call_turn, answer_turn]), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    result = await loop.run(ctx=_ctx(), skill_state=_state(registry), user_text="뭐 할 수 있어?", prompt_context_snippets=[])
    assert result.text == "안내함"


@pytest.mark.asyncio
async def test_agent_loop_scopes_registry_snapshot_to_declared_skill_tool() -> None:
    # 핵심 회귀 테스트: core/mention.py가 만드는 ToolContext는
    # services={}이고, AgentLoop가 그걸 그대로 돌려쓰면 admin_skills처럼 manifest.services에
    # registry_snapshot을 선언한 스킬 도구가 멘션 경로에서는 항상 "스킬 현황을 아직 알 수
    # 없다"로 실패했다(슬래시 경로는 core/slash.py._player_context가 따로 채워 줘서 멀쩡했다).
    # AgentLoop(registry_snapshot=...)로 넘긴 스냅샷이 실제로 admin 스킬 도구까지 도달하는지 본다.
    registry = _registry()
    snapshot = build_snapshot(registry)
    call_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="admin_skills", args={})],
        usage=UsageInfo(input_tokens=10, output_tokens=1),
    )
    seen_function_responses: list[dict[str, object]] = []

    def script(
        model: str, system: str, contents: list[LlmContent], tools: list[ToolDeclaration]
    ) -> GenerationTurn:
        if len(contents) == 1:
            return call_turn
        for part in contents[-1].parts:
            if part.function_response is not None:
                seen_function_responses.append(part.function_response.response)
        return GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="봤다")]), text="봤다")

    loop = AgentLoop(
        llm=FakeLlmAdapter(script), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
        registry_snapshot=snapshot,
    )
    # admin 스킬은 tags=[admin]을 요구한다 — admin 태그를 준다.
    ctx = ToolContext(player_id="p1", tags=frozenset({"admin"}), services={})
    result = await loop.run(
        ctx=ctx, skill_state=_state(registry, mode="full"), user_text="스킬 현황 보여줘", prompt_context_snippets=[]
    )

    assert result.text == "봤다"
    response = seen_function_responses[0]
    assert "error" not in response  # 이전 버그에서는 "스킬 현황을 아직 알 수 없다"로 막혔다
    enabled = response["enabled"]
    assert isinstance(enabled, list)
    assert "chunithm-border" in enabled  # registry_snapshot이 실제 등록 현황을 담고 왔다는 증거


@pytest.mark.asyncio
async def test_agent_loop_run_surfaces_required_mode_display_in_result() -> None:
    # C1 회귀: admin_skills(display_mode="required", 기본값)를 실제로 멘션 경로와 같은
    # AgentLoop.run()으로 불러, AgentTurnResult.required_displays에 그 표시물이 실제로
    # 담기는지 본다(이전에는 required 표시물을 담을 자리 자체가 없어 통째로 버려졌다).
    registry = _registry()
    snapshot = build_snapshot(registry)
    call_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="admin_skills", args={})],
        usage=UsageInfo(input_tokens=10, output_tokens=1),
    )
    answer_turn = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="봤다")]), text="봤다")
    loop = AgentLoop(
        llm=FakeLlmAdapter([call_turn, answer_turn]), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
        registry_snapshot=snapshot,
    )
    ctx = ToolContext(player_id="p1", tags=frozenset({"admin"}), services={})
    result = await loop.run(
        ctx=ctx, skill_state=_state(registry, mode="full"), user_text="스킬 현황 보여줘", prompt_context_snippets=[]
    )

    assert result.text == "봤다"
    assert len(result.required_displays) == 1
    assert result.required_displays[0].content is not None
    assert "chunithm-border" in result.required_displays[0].content


@pytest.mark.asyncio
async def test_agent_loop_keeps_only_the_last_omittable_display_of_the_same_tool() -> None:
    # 삭제 전후로 목록 도구를 두 번 부르면 마지막 목록만 붙는다(실기 2026-10-20: 남은 목표가 두 번 나왔다).
    registry = _registry()
    calls = [
        GenerationTurn(
            content=LlmContent(role="model", parts=[]),
            function_calls=[FunctionCallRequest(call_id=str(i), name="admin_skills", args={})],
        )
        for i in (1, 2)
    ]
    answer = GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="끝")]), text="끝")
    loop = AgentLoop(
        llm=FakeLlmAdapter([*calls, answer]), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
        registry_snapshot=build_snapshot(registry),
    )
    served = iter(["첫 목록", "마지막 목록"])

    async def fake_invoke(ctx: ToolContext, tool_name: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult(display=Display(content=next(served), omittable=True), display_mode="required")

    loop._invoke = fake_invoke  # pyright: ignore[reportPrivateUsage]
    ctx = ToolContext(player_id="p1", tags=frozenset({"admin"}), services={})
    result = await loop.run(
        ctx=ctx, skill_state=_state(registry, mode="full"), user_text="x", prompt_context_snippets=[]
    )

    assert [d.content for d in result.required_displays] == ["마지막 목록"]


class _FakeMemoryService:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    async def get(self, player_id: str) -> str:
        return self.saved.get(player_id, "")

    async def set(self, player_id: str, content: str) -> None:
        self.saved[player_id] = content

    async def remember(
        self, player_id: str, *, content: str | None = None, nickname: str | None = None
    ) -> RememberResult:
        if content is not None:
            self.saved[player_id] = content
        return RememberResult(content_saved=content, nickname_saved=nickname)


@pytest.mark.asyncio
async def test_agent_loop_core_tool_remember_resolves_memory_service() -> None:
    # remember는 core 도구(skill_name=None)라 manifest 선언 범위에 걸리지 않고, executor에
    # 등록된 실제 "memory" 서비스(core/bot.py.build_client가 쓰는 바로 그 키 이름)를 받아야
    # 한다. 이전 버그에서는 core/mention.py의 빈 services={}가 그대로 내려와 ctx.memory 접근이
    # UndeclaredServiceError로 터졌다(에러가 함수 응답의 "error" 키로 모델에 전달됐다).
    registry = _registry()
    memory = _FakeMemoryService()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(), services={"memory": memory},
    )
    call_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="remember", args={"content": "민트초코 좋아함"})],
        usage=UsageInfo(input_tokens=10, output_tokens=1),
    )
    seen_function_responses: list[dict[str, object]] = []

    def script(
        model: str, system: str, contents: list[LlmContent], tools: list[ToolDeclaration]
    ) -> GenerationTurn:
        if len(contents) == 1:
            return call_turn
        for part in contents[-1].parts:
            if part.function_response is not None:
                seen_function_responses.append(part.function_response.response)
        return GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="기억했다")]), text="기억했다")

    loop = AgentLoop(
        llm=FakeLlmAdapter(script), executor=executor,
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    result = await loop.run(
        ctx=_ctx(), skill_state=_state(registry), user_text="민트초코 좋아하는 거 기억해줘", prompt_context_snippets=[]
    )

    assert result.text == "기억했다"
    response = seen_function_responses[0]
    assert "error" not in response
    assert response["saved"] == "민트초코 좋아함"
    assert memory.saved["p1"] == "민트초코 좋아함"


def _write_scope_probe_skill(root: Path, name: str, *, services: list[str]) -> Path:
    """manifest.services에 적은 이름만 ctx.services 딕셔너리에 실제로 들어오는지 보는
    테스트 전용 스킬. tests/core/skill_factory.write_skill은 services: []를 고정해 두므로
    (스코프 자체를 시험하는 이 테스트에는 안 맞는다) 여기서 manifest를 직접 만든다."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    services_yaml = "[" + ", ".join(services) + "]"
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: 테스트용 서비스 스코프 확인 스킬
examples:
  - 예시
category: 메모
game: common
version: 1
services: {services_yaml}
---

테스트용.
"""
    )
    (skill_dir / "skill.py").write_text(
        "from core.decorators import tool\n"
        "from core.tool_types import ToolContext, ToolResult\n\n\n"
        "@tool()\n"
        "async def check_scope(ctx: ToolContext) -> ToolResult:\n"
        '    """테스트용: ctx.services에 실제로 들어온 키만 그대로 보여준다."""\n'
        "    return ToolResult(data={\"services\": sorted(ctx.services.keys())})\n"
    )
    return skill_dir


@pytest.mark.asyncio
async def test_agent_loop_does_not_leak_undeclared_services_into_skill_tool_ctx(tmp_path: Path) -> None:
    # 보안 회귀 테스트: executor.services에는 "terms"/"players"/"settings" 셋 다 등록돼 있지만,
    # 이 스킬은 manifest.services에 "terms"만 선언했다 — 스킬 도구에 실제로 전달되는 scoped
    # ToolContext.services 딕셔너리 내용물이 선언한 것 하나만 담고 있어야 한다(신뢰로 때우지
    # 않고 실제 딕셔너리 내용을 직접 확인한다).
    skills_root = tmp_path / "skills"
    _write_scope_probe_skill(skills_root, "scope-check", services=["terms"])

    registry = SkillRegistry([Path("skills"), skills_root])
    registry.load()
    assert "scope-check" in {s.manifest.name for s in registry.enabled_skills()}, registry.failures()

    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(),
        services={"terms": object(), "players": object(), "settings": object()},
    )
    call_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="scope-check_check_scope", args={})],
        usage=UsageInfo(input_tokens=10, output_tokens=1),
    )
    seen_function_responses: list[dict[str, object]] = []

    def script(
        model: str, system: str, contents: list[LlmContent], tools: list[ToolDeclaration]
    ) -> GenerationTurn:
        if len(contents) == 1:
            return call_turn
        for part in contents[-1].parts:
            if part.function_response is not None:
                seen_function_responses.append(part.function_response.response)
        return GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="봤다")]), text="봤다")

    loop = AgentLoop(
        llm=FakeLlmAdapter(script), executor=executor,
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    result = await loop.run(
        ctx=_ctx(), skill_state=_state(registry, mode="full"), user_text="테스트", prompt_context_snippets=[]
    )

    assert result.text == "봤다"
    response = seen_function_responses[0]
    assert "error" not in response
    assert response["services"] == ["terms"]  # "players"/"settings"는 선언 안 했으니 안 보여야 한다


_PROMPT_CONTEXT_BODY = (
    "from core.decorators import prompt_context\n\n\n"
    "@prompt_context(max_chars=20)\n"
    "async def remembers(ctx: ToolContext) -> str | None:\n"
    '    """테스트용 맥락."""\n'
    "    return '가' * 50\n\n\n"
    "@prompt_context()\n"
    "async def quiet(ctx: ToolContext) -> str | None:\n"
    '    """테스트용: 할 말이 없다."""\n'
    "    return None\n\n\n"
    "@prompt_context()\n"
    "async def broken(ctx: ToolContext) -> str | None:\n"
    '    """테스트용: 실패한다."""\n'
    '    raise RuntimeError("boom")\n'
)


async def _prompt_context_system_instruction(tmp_path: Path) -> str:
    skills_root = tmp_path / "skills"
    _ = write_skill(skills_root, "test-context", _PROMPT_CONTEXT_BODY)
    registry = SkillRegistry([skills_root])
    registry.load()
    llm = FakeLlmAdapter(
        [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")]
    )
    loop = AgentLoop(
        llm=llm, executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    _ = await loop.run(ctx=_ctx(), skill_state=_state(registry), user_text="안녕", prompt_context_snippets=["기존 맥락"])
    return llm.calls[0][1]


@pytest.mark.asyncio
async def test_skill_prompt_context_is_added_and_capped(tmp_path: Path) -> None:
    system_instruction = await _prompt_context_system_instruction(tmp_path)

    assert "기존 맥락" in system_instruction
    assert "가" * 20 in system_instruction
    assert "가" * 21 not in system_instruction


@pytest.mark.asyncio
async def test_a_failing_or_silent_prompt_context_does_not_stop_the_reply(tmp_path: Path) -> None:
    system_instruction = await _prompt_context_system_instruction(tmp_path)

    assert "boom" not in system_instruction
