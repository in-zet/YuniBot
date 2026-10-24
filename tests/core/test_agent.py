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
from core.tables import RowRef, TableBuilder, TableStore, cell
from core.tool_types import ToolContext
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


def _ctx(tags: frozenset[str] = frozenset()) -> ToolContext:
    return ToolContext(player_id="p1", tags=tags, services={})


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


class _FakeMemoryService:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    async def get(self, player_id: str) -> str:
        return self.saved.get(player_id, "")

    async def set(self, player_id: str, content: str) -> None:
        self.saved[player_id] = content

    async def remember(
        self, player_id: str, *, content: str | None = None, nickname: str | None = None, game: str | None = None
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
games: []
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


def _write_tables_probe_skill(root: Path, name: str, *, services: list[str]) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    services_yaml = "[" + ", ".join(services) + "]"
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 테스트용 표 보관소 확인 스킬\nexamples:\n  - 예시\n"
        f"category: 메모\ngames: []\nversion: 1\nservices: {services_yaml}\n---\n\n테스트용.\n"
    )
    (skill_dir / "skill.py").write_text(
        "from core.decorators import tool\n"
        "from core.tool_types import ToolContext, ToolResult\n\n\n"
        "@tool()\n"
        "async def probe(ctx: ToolContext) -> ToolResult:\n"
        '    """테스트용: 표 보관소와 채널 id를 그대로 보여준다."""\n'
        "    return ToolResult(data={'channel': ctx.channel_id})\n"
    )
    return skill_dir


@pytest.mark.asyncio
async def test_agent_loop_passes_channel_id_to_skill_tools_and_gates_tables(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _ = _write_tables_probe_skill(skills_root, "tables-probe", services=["tables"])
    registry = SkillRegistry([Path("skills"), skills_root])
    registry.load()
    assert "tables-probe" in {s.manifest.name for s in registry.enabled_skills()}, registry.failures()

    store = TableStore()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(), tables=store,
    )
    seen: list[ToolContext] = []
    original = executor.invoke

    async def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["ctx"] if "ctx" in kwargs else args[1])
        return await original(*args, **kwargs)

    executor.invoke = spy  # type: ignore[method-assign]  # pyright: ignore[reportAttributeAccessIssue]
    call_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="tables-probe_probe", args={})],
        usage=UsageInfo(input_tokens=10, output_tokens=1),
    )

    def script(
        model: str, system: str, contents: list[LlmContent], tools: list[ToolDeclaration]
    ) -> GenerationTurn:
        if len(contents) == 1:
            return call_turn
        return GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="봤다")]), text="봤다")

    loop = AgentLoop(
        llm=FakeLlmAdapter(script), executor=executor,
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, channel_id="c9")
    _ = await loop.run(ctx=ctx, skill_state=_state(registry, mode="full"), user_text="테스트", prompt_context_snippets=[])

    assert len(seen) == 1
    assert seen[0].channel_id == "c9"
    assert seen[0].tables is store


@pytest.mark.asyncio
async def test_agent_loop_hides_tables_from_skill_that_did_not_declare_them(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _ = _write_tables_probe_skill(skills_root, "tables-probe", services=[])
    registry = SkillRegistry([Path("skills"), skills_root])
    registry.load()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(), tables=TableStore(),
    )
    agent = AgentLoop(
        llm=FakeLlmAdapter(lambda *_: GenerationTurn(content=LlmContent(role="model", parts=[]), text="")),
        executor=executor,
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    scoped = agent._scoped_ctx(  # pyright: ignore[reportPrivateUsage]
        ToolContext(player_id="p1", tags=frozenset(), services={}, channel_id="c9"),
        skill_name="tables-probe", declared_services=[],
    )
    assert scoped.tables is None
    assert scoped.channel_id == "c9"


_TABLE_SKILL_BODY = (
    "from typing import Annotated\n"
    "from core.decorators import tool\n"
    "from core.tables import RowRef, TableBuilder, TableNeeds, cell\n"
    "from core.tool_types import TableRef, ToolContext, ToolResult\n\n\n"
    "@tool()\n"
    "async def make(ctx: ToolContext) -> ToolResult:\n"
    '    """테스트용: 표를 만든다."""\n'
    '    builder = TableBuilder(subject="p1", unit="채보").column("song", "곡").column("score", "점수", "number")\n'
    '    builder.column("id", "아이디", slash_only=True)\n'
    '    builder.group("A", {"평균": "1007500"})\n'
    '    builder.row(RowRef("chunithm.chart", "secret-key"), {"song": cell("크로노"), "score": cell(1007500, "1,007,500"), "id": cell("g1")})\n'
    '    builder.group("B")\n'
    '    builder.row(RowRef("chunithm.chart", "k2"), {"song": cell("B곡"), "score": cell(900000, "900,000")})\n'
    '    return ToolResult(data={}, table=builder.summary({"레이팅": "17.04"}).step("테스트").build())\n\n\n'
    "@tool()\n"
    "async def echo(ctx: ToolContext, table: Annotated[TableRef, TableNeeds(nonempty=['song'])]) -> ToolResult:\n"
    '    """테스트용: 받은 표를 그대로 새 표로 돌려준다."""\n'
    "    return ToolResult(table=TableBuilder.from_table(table).build())\n\n\n"
    "@tool()\n"
    "async def draw(ctx: ToolContext, table: TableRef) -> ToolResult:\n"
    '    """테스트용: 표를 그림으로 그렸다고 알린다."""\n'
    "    return ToolResult(data={'drawn': True}, drew_table=table.id)\n\n\n"
    "@tool()\n"
    "async def broken_table(ctx: ToolContext) -> ToolResult:\n"
    '    """테스트용: 표와 함께 에러를 낸다."""\n'
    '    return ToolResult(error="실패", table=TableBuilder().column("a").build())\n'
)


async def _run_table_turns(
    tmp_path: Path, calls: list[tuple[str, dict[str, Any]]], *, store: TableStore | None = None,
    channel_id: str | None = "c1", before: Any = None,
) -> tuple[Any, list[dict[str, object]], TableStore]:
    skills_root = tmp_path / "skills"
    _ = write_skill(skills_root, "table-skill", _TABLE_SKILL_BODY)
    registry = SkillRegistry([skills_root])
    registry.load()
    assert "table-skill" in {s.manifest.name for s in registry.enabled_skills()}, registry.failures()
    store = store if store is not None else TableStore()
    if before is not None:
        before(store)
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(), tables=store,
    )
    seen: list[dict[str, object]] = []
    queue = list(calls)

    def script(
        model: str, system: str, contents: list[LlmContent], tools: list[ToolDeclaration]
    ) -> GenerationTurn:
        last = contents[-1]
        for part in last.parts:
            if part.function_response is not None:
                seen.append(part.function_response.response)
        if queue:
            name, args = queue.pop(0)
            return GenerationTurn(
                content=LlmContent(role="model", parts=[]),
                function_calls=[FunctionCallRequest(call_id=str(len(seen)), name=name if name.startswith("table_") else f"table-skill_{name}", args=args)],
            )
        return GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="끝")]), text="끝")

    loop = AgentLoop(
        llm=FakeLlmAdapter(script), executor=executor,
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, channel_id=channel_id)
    result = await loop.run(
        ctx=ctx, skill_state=_state(registry, mode="full"), user_text="x", prompt_context_snippets=[]
    )
    return result, seen, store


@pytest.mark.asyncio
async def test_table_result_is_stored_and_the_model_gets_only_the_summary(tmp_path: Path) -> None:
    result, seen, store = await _run_table_turns(tmp_path, [("make", {})])

    assert seen == [
        {
            "table": "t1", "rows": 2, "unit": "채보", "columns": ["곡", "점수"],
            "groups": [{"title": "A", "rows": 1, "summary": {"평균": "1007500"}}, {"title": "B", "rows": 1}],
            "steps": ["테스트"], "summary": {"레이팅": "17.04"},
        }
    ]
    assert result.turn_table_ids == ["t1"]
    assert result.last_table_id == "t1"
    assert result.drawn_table_ids == []
    stored = store.get("p1", "c1", "t1")
    assert stored is not None and stored.row_count == 2
    # 모델이 받지 않은 값은 숫자 대조 자료에도 없다
    assert result.collected_data == seen
    assert "1,007,500" not in str(result.collected_data) and "secret-key" not in str(result.collected_data)
    assert result.required_displays == []


@pytest.mark.asyncio
async def test_table_query_makes_a_new_table_from_an_earlier_turn_table(tmp_path: Path) -> None:
    def seed(store: TableStore) -> None:
        builder = TableBuilder(subject="p1").column("song", "곡").column("score", "점수", "number")
        builder.row(RowRef("chunithm.chart", "1"), {"song": cell("A"), "score": cell(10)})
        builder.row(RowRef("chunithm.chart", "2"), {"song": cell("B"), "score": cell(30)})
        builder.row(RowRef("chunithm.chart", "3"), {"song": cell("C"), "score": cell(20)})
        _ = store.put("p1", "c1", builder.build(), now=0)

    args = {"table": "t1", "where": [{"column": "점수", "op": ">=", "value": "20"}], "sort": "점수", "descending": True}
    result, seen, store = await _run_table_turns(tmp_path, [("table_query", args)], before=seed)

    assert seen[0]["table"] == "t2" and seen[0]["rows"] == 2
    assert seen[0]["steps"] == ["점수>=20", "점수 내림차순"]
    assert result.last_table_id == "t2"
    new = store.get("p1", "c1", "t2")
    assert new is not None and [r.cell("song").raw for r in new.rows] == ["B", "C"]
    original = store.get("p1", "c1", "t1")
    assert original is not None and original.row_count == 3


@pytest.mark.asyncio
async def test_table_query_rows_on_a_table_made_this_turn_is_refused(tmp_path: Path) -> None:
    result, seen, _store = await _run_table_turns(
        tmp_path, [("make", {}), ("table_query", {"table": "t1", "rows": [1]})]
    )

    assert seen[1] == {"error": "번호는 사람이 본 표에만 쓴다"}
    assert result.turn_table_ids == ["t1"]


@pytest.mark.asyncio
async def test_table_peek_gives_raw_values_to_the_model_and_the_number_check(tmp_path: Path) -> None:
    def seed(store: TableStore) -> None:
        builder = TableBuilder().column("song", "곡").column("score", "점수", "number")
        builder.row(RowRef("chunithm.chart", "1"), {"song": cell("A"), "score": cell(1_005_000, "1,005,000")})
        _ = store.put("p1", "c1", builder.build(), now=0)

    result, seen, _store = await _run_table_turns(tmp_path, [("table_peek", {"table": "t1", "rows": [1]})], before=seed)

    assert seen[0]["rows"] == [{"number": 1, "values": {"곡": "A", "점수": 1_005_000}}]
    assert result.collected_data == seen
    assert result.last_table_id is None and result.turn_table_ids == []


@pytest.mark.asyncio
async def test_a_table_drawn_by_a_picture_tool_is_recorded_as_drawn(tmp_path: Path) -> None:
    result, seen, _store = await _run_table_turns(tmp_path, [("make", {}), ("draw", {"table": "t1"})])

    assert seen[1] == {"drawn": True}
    assert result.last_table_id == "t1"
    assert result.drawn_table_ids == ["t1"]


@pytest.mark.asyncio
async def test_last_table_follows_the_latest_table_of_the_turn(tmp_path: Path) -> None:
    result, seen, _store = await _run_table_turns(tmp_path, [("make", {}), ("echo", {"table": "t1"})])

    assert result.turn_table_ids == ["t1", "t2"]
    assert result.last_table_id == "t2"
    assert seen[1]["table"] == "t2"


@pytest.mark.asyncio
async def test_number_on_a_table_made_this_turn_is_refused(tmp_path: Path) -> None:
    result, seen, _store = await _run_table_turns(tmp_path, [("make", {}), ("echo", {"table": "t1:1"})])

    assert seen[1] == {"error": "번호는 사람이 본 표에만 쓴다"}
    assert result.turn_table_ids == ["t1"]


@pytest.mark.asyncio
async def test_number_on_a_table_from_an_earlier_turn_is_allowed(tmp_path: Path) -> None:
    def seed(store: TableStore) -> None:
        builder = TableBuilder().column("song", "곡")
        builder.row(RowRef("chunithm.chart", "1"), {"song": cell("A")})
        builder.row(RowRef("chunithm.chart", "2"), {"song": cell("B")})
        _ = store.put("p1", "c1", builder.build(), now=0)

    result, seen, _store = await _run_table_turns(tmp_path, [("echo", {"table": "t1:2"})], before=seed)

    assert seen[0]["rows"] == 1
    assert seen[0]["table"] == "t2"
    assert seen[0]["steps"] == ["2번"]
    assert result.last_table_id == "t2"


@pytest.mark.asyncio
async def test_table_made_from_a_stale_table_is_stale(tmp_path: Path) -> None:
    def seed(store: TableStore) -> None:
        builder = TableBuilder(subject="p1").column("song", "곡")
        builder.row(RowRef("chunithm.chart", "1"), {"song": cell("A")})
        _ = store.put("p1", "c1", builder.build(), now=0)
        store.mark_stale("p1")

    result, seen, store = await _run_table_turns(
        tmp_path, [("echo", {"table": "t1"}), ("make", {})], before=seed
    )

    assert seen[0]["stale"] is True
    assert "stale" not in seen[1]
    assert store.is_stale("p1", "c1", "t2")
    assert not store.is_stale("p1", "c1", "t3")
    assert result.last_table_id == "t3"


@pytest.mark.asyncio
async def test_tool_error_with_table_stores_nothing(tmp_path: Path) -> None:
    result, seen, store = await _run_table_turns(tmp_path, [("broken_table", {})])

    assert seen == [{"error": "실패"}]
    assert result.last_table_id is None and result.turn_table_ids == []
    assert store.ids("p1", "c1") == []


@pytest.mark.asyncio
async def test_table_cannot_be_kept_without_a_channel(tmp_path: Path) -> None:
    result, seen, _store = await _run_table_turns(tmp_path, [("make", {})], channel_id=None)

    assert seen == [{"error": "이 자리에서는 표를 보관할 수 없다"}]
    assert result.last_table_id is None


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


def _loop(registry: SkillRegistry, turns: list[GenerationTurn]) -> AgentLoop:
    return AgentLoop(
        llm=FakeLlmAdapter(turns), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=5, max_tokens=50000,
    )


@pytest.mark.asyncio
async def test_agent_loop_sums_cached_input_tokens_and_logs_them(caplog: pytest.LogCaptureFixture) -> None:
    registry = _registry()
    call_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="open_skill", args={"skill_name": "chunithm-border"})],
        usage=UsageInfo(input_tokens=50, output_tokens=5, cached_input_tokens=30),
    )
    answer_turn = GenerationTurn(
        content=LlmContent(role="model", parts=[LlmPart(text="끝")]), text="끝",
        usage=UsageInfo(input_tokens=60, output_tokens=5, cached_input_tokens=45),
    )
    loop = _loop(registry, [call_turn, answer_turn])

    with caplog.at_level("INFO", logger="core.agent"):
        result = await loop.run(
            ctx=_ctx(), skill_state=_state(registry, mode="full"), user_text="질문", prompt_context_snippets=[]
        )

    assert (result.usage.input_tokens, result.usage.cached_input_tokens) == (110, 75)
    assert "토큰 입력 50(캐시 30)" in caplog.text
    assert "누적 토큰 입력 110(캐시 75)" in caplog.text


def test_skills_used_picks_skills_from_tool_names_in_order_without_duplicates() -> None:
    registry = _registry()
    loop = _loop(registry, [])

    used = loop._skills_used(
        ["open_skill", "song_find_songs", "table_query", "image_table_image", "song_info"],
        set(),
    )

    assert used == ["song", "image"]


def _loop(registry: SkillRegistry, script: list[GenerationTurn], *, max_tokens: int = 50000) -> AgentLoop:
    return AgentLoop(
        llm=FakeLlmAdapter(script), executor=_executor(registry),
        prompt=PromptAssembler(rules_text="규칙", persona_text="말투", registry=registry),
        model="gemini-3.8-flash", thinking_level="low", max_tool_calls=10, max_tokens=max_tokens,
    )


def _noop_call_turn(usage: UsageInfo, model: str | None = None) -> GenerationTurn:
    return GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="remember", args={"text": "x"})],
        usage=usage, model=model,
    )


@pytest.mark.asyncio
async def test_agent_loop_splits_usage_by_model_when_fallback_answers_mid_turn() -> None:
    registry = _registry()
    script = [
        _noop_call_turn(UsageInfo(input_tokens=100, output_tokens=5, cached_input_tokens=40)),
        GenerationTurn(
            content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답",
            usage=UsageInfo(input_tokens=200, output_tokens=7), model="gemini-3.5-flash-lite",
        ),
    ]
    result = await _loop(registry, script).run(
        ctx=_ctx(), skill_state=_state(registry), user_text="안녕", prompt_context_snippets=[]
    )
    assert result.usage_by_model["gemini-3.8-flash"] == UsageInfo(input_tokens=100, output_tokens=5, cached_input_tokens=40)
    assert result.usage_by_model["gemini-3.5-flash-lite"] == UsageInfo(input_tokens=200, output_tokens=7)
    assert result.usage.input_tokens == 300


@pytest.mark.asyncio
async def test_token_cap_counts_cached_input_at_ten_percent() -> None:
    registry = _registry()

    def turns(cached: int) -> list[GenerationTurn]:
        usage = UsageInfo(input_tokens=30000, output_tokens=10, cached_input_tokens=cached)
        return [
            _noop_call_turn(usage), _noop_call_turn(usage),
            GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="끝")]), text="끝", usage=usage),
        ]

    # 입력 9만 토큰. 캐시가 없으면 상한(5만)에 걸리고, 27K씩 캐시되면 환산 입력 약 2.4만이라 끝까지 간다.
    uncached = await _loop(registry, turns(0), max_tokens=50000).run(
        ctx=_ctx(), skill_state=_state(registry), user_text="안녕", prompt_context_snippets=[]
    )
    cached = await _loop(registry, turns(27000), max_tokens=50000).run(
        ctx=_ctx(), skill_state=_state(registry), user_text="안녕", prompt_context_snippets=[]
    )
    assert uncached.hit_token_cap is True
    assert cached.hit_token_cap is False
    assert cached.text == "끝"


@pytest.mark.asyncio
async def test_agent_loop_pre_opens_skills_whose_triggers_match_the_message() -> None:
    registry = _registry()
    script = [GenerationTurn(content=LlmContent(role="model", parts=[LlmPart(text="답")]), text="답")]
    state = _state(registry, mode="full")
    await _loop(registry, script).run(
        ctx=_ctx(frozenset({"sega-linked"})), skill_state=state, user_text="내 즐겨찾기 보여줘",
        prompt_context_snippets=[],
    )
    assert state.level_for("settings") == "full"
    assert state.level_for("chunithm-border") == "card"


@pytest.mark.asyncio
async def test_agent_loop_warns_when_one_round_prompt_is_large(caplog: pytest.LogCaptureFixture) -> None:
    """왕복 한 번의 입력이 8만 토큰을 넘으면 경고한다(10만 초과 시 단가가 오르는 모델이 있다)."""
    registry = _registry()
    small = GenerationTurn(
        content=LlmContent(role="model", parts=[]),
        function_calls=[FunctionCallRequest(call_id="1", name="open_skill", args={"skill_name": "chunithm-border"})],
        usage=UsageInfo(input_tokens=79_000, output_tokens=5),
    )
    large = GenerationTurn(
        content=LlmContent(role="model", parts=[LlmPart(text="끝")]), text="끝",
        usage=UsageInfo(input_tokens=81_000, output_tokens=5),
    )
    loop = _loop(registry, [small, large], max_tokens=1_000_000)

    with caplog.at_level("INFO", logger="core.agent"):
        await loop.run(ctx=_ctx(), skill_state=_state(registry, mode="full"), user_text="질문", prompt_context_snippets=[])

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING" and "단가 상승" in r.getMessage()]
    assert len(warnings) == 1
    assert "2회차 입력 81000토큰" in warnings[0]
