# core/agent.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from core.core_tools import CORE_TOOL_SPECS
from core.decorators import ToolSpec
from core.executor import ToolExecutor
from core.llm import (
    FunctionResultPart,
    LlmAdapter,
    LlmContent,
    LlmPart,
    UsageInfo,
)
from core.prompt import PromptAssembler
from core.registry import LoadedSkill
from core.registry_snapshot import RegistrySnapshot
from core.skill_loading import SkillLoadState
from core.tool_types import Display, ToolContext, ToolResult

_CORE_TOOLS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in CORE_TOOL_SPECS}


@dataclass(slots=True, kw_only=True)
class AgentTurnResult:
    text: str | None = None
    collected_data: list[dict[str, Any]] = field(default_factory=list)
    optional_displays: dict[str, Display] = field(default_factory=dict)
    required_displays: list[Display] = field(default_factory=list)
    usage: UsageInfo = field(default_factory=lambda: UsageInfo(input_tokens=0, output_tokens=0))
    hit_tool_call_cap: bool = False
    hit_token_cap: bool = False


class AgentLoop:
    """LLM과 ToolExecutor를 왕복시켜 한 번의 멘션 응답을 만든다.

    도구 실행 로직은 전혀 갖지 않는다 — Gemini 함수 호출 이름을 ToolSpec으로 되짚어
    ToolExecutor.invoke에 넘기는 것까지만 한다.
    """

    def __init__(
        self,
        *,
        llm: LlmAdapter,
        executor: ToolExecutor,
        prompt: PromptAssembler,
        model: str,
        thinking_level: str,
        max_tool_calls: int,
        max_tokens: int,
        db: aiosqlite.Connection | None = None,
        registry_snapshot: RegistrySnapshot | None = None,
    ) -> None:
        self._llm = llm
        self._executor = executor
        self._prompt = prompt
        self._model = model
        self._thinking_level = thinking_level
        self._max_tool_calls = max_tool_calls
        self._max_tokens = max_tokens
        self._db = db
        self._registry_snapshot = registry_snapshot

    def _registry_skills(self) -> list[LoadedSkill]:
        # PromptAssembler는 registry를 공개 속성으로 내지 않는다. 같은 core/ 패키지 안의
        # 협력이라 private 접근을 쓴다.
        return self._prompt._registry.enabled_skills()

    def _find_spec(self, tool_name: str) -> tuple[ToolSpec, str | None] | None:
        if tool_name in _CORE_TOOLS_BY_NAME:
            return _CORE_TOOLS_BY_NAME[tool_name], None
        for skill in self._registry_skills():
            prefix = f"{skill.manifest.name}_"
            if tool_name.startswith(prefix):
                fn_name = tool_name[len(prefix):]
                for spec in skill.code.tools:
                    if spec.name == fn_name:
                        return spec, skill.manifest.name
        return None

    async def run(
        self,
        *,
        ctx: ToolContext,
        skill_state: SkillLoadState,
        user_text: str,
        prompt_context_snippets: list[str],
    ) -> AgentTurnResult:
        inner_ctx = ToolContext(
            player_id=ctx.player_id,
            tags=ctx.tags,
            services={**ctx.services, "_skill_load_state": skill_state},
            guild_id=ctx.guild_id,
            event_bus=ctx.event_bus,
            player_data=ctx.player_data,
            db=ctx.db,
            registry_snapshot=ctx.registry_snapshot,
            usage=ctx.usage,
            discord_id=ctx.discord_id,
        )

        contents: list[LlmContent] = [LlmContent(role="user", parts=[LlmPart(text=user_text)])]
        result = AgentTurnResult()
        tool_calls_made = 0

        while True:
            player_tags = set(ctx.tags)
            system_instruction = self._prompt.build_system_instruction(
                player_tags=player_tags,
                skill_state=skill_state,
                prompt_context_snippets=prompt_context_snippets,
            )
            tools = self._prompt.build_tool_declarations(player_tags=player_tags, skill_state=skill_state)

            turn = await self._llm.generate(
                model=self._model,
                system_instruction=system_instruction,
                contents=contents,
                tools=tools,
                thinking_level=self._thinking_level,
            )
            result.usage = UsageInfo(
                input_tokens=result.usage.input_tokens + turn.usage.input_tokens,
                output_tokens=result.usage.output_tokens + turn.usage.output_tokens,
            )
            if result.usage.input_tokens + result.usage.output_tokens > self._max_tokens:
                result.hit_token_cap = True
                result.text = turn.text
                return result

            if not turn.function_calls:
                result.text = turn.text
                return result

            # 모델 자신의 턴은 raw(제공자 Content, thought signature 포함)를 그대로 보존해야
            # 하므로 turn.content를 그대로 append한다 — LlmContent를 새로 만들지 않는다.
            contents.append(turn.content)

            response_parts: list[LlmPart] = []
            for call in turn.function_calls:
                tool_calls_made += 1
                if tool_calls_made > self._max_tool_calls:
                    result.hit_tool_call_cap = True
                    result.text = turn.text
                    return result

                tool_result = await self._invoke(inner_ctx, call.name, call.args)
                if tool_result.data:
                    result.collected_data.append(tool_result.data)
                if tool_result.display is not None and tool_result.display_mode == "optional":
                    display_id = f"d{len(result.optional_displays) + 1}"
                    result.optional_displays[display_id] = tool_result.display
                    tool_result.data["_display_id"] = display_id
                elif tool_result.display is not None and tool_result.display_mode == "required":
                    # required는 00-core.md의 기본 표시 모드다 — 숫자 위반 검사와 무관하게
                    # 항상 붙는다(assemble_response가 이 리스트를 조건 없이 그대로 포함한다).
                    result.required_displays.append(tool_result.display)
                response_parts.append(
                    LlmPart(
                        function_response=FunctionResultPart(
                            name=call.name,
                            response={"error": tool_result.error} if tool_result.error else tool_result.data,
                            call_id=call.call_id,
                        )
                    )
                )
            # 함수 응답 턴은 모델 턴 뒤에, 별도의 새 LlmContent(role="user")로 붙인다
            # (call-parts가 response-parts보다 항상 먼저 와야 하는 thought-signature 순서 규칙).
            contents.append(LlmContent(role="user", parts=response_parts))

    async def _invoke(self, ctx: ToolContext, tool_name: str, args: dict[str, Any]) -> ToolResult:
        if tool_name == "list_capabilities":
            from core.capabilities import build_capabilities_text

            text = build_capabilities_text(
                registry=self._prompt._registry,
                player_tags=set(ctx.tags), tag_definitions=self._prompt._registry.tag_definitions(),
                category=args.get("category"),
            )
            return ToolResult(data={"capabilities": text}, display=Display(content=text, ephemeral=True))

        found = self._find_spec(tool_name)
        if found is None:
            return ToolResult(error=f"'{tool_name}'이라는 도구가 없다")
        spec, skill_name = found

        skill_tags: list[str] = []
        skill_tags_any: list[str] = []
        declared_services: list[str] = []
        if skill_name is not None:
            for skill in self._registry_skills():
                if skill.manifest.name == skill_name:
                    skill_tags = list(skill.manifest.tags)
                    skill_tags_any = list(skill.manifest.tags_any)
                    declared_services = list(skill.manifest.services)
                    break

        scoped_ctx = self._scoped_ctx(ctx, skill_name=skill_name, declared_services=declared_services)

        outcome = await self._executor.invoke(
            spec, scoped_ctx, args, skill_tags=skill_tags, skill_tags_any=skill_tags_any, skill_name=skill_name,
        )
        if outcome.needs_confirm:
            # 멘션 경로에서 confirm="always" 도구(와 2건 이상을 바꾸는 confirm="bulk" 도구)는
            # 디스코드 버튼을 낼 인터랙션이 없다
            # (대화 스레드 중이라 상호작용 컨텍스트가 없다) — 바로 거부하고 슬래시로 안내한다.
            return ToolResult(error="이 작업은 슬래시 명령으로만 확인할 수 있다")
        assert outcome.result is not None
        return outcome.result

    def _scoped_ctx(
        self, ctx: ToolContext, *, skill_name: str | None, declared_services: list[str]
    ) -> ToolContext:
        """도구 호출 하나마다 scope를 좁힌 ToolContext를 새로 만든다.

        core 도구(open_skill/remember — list_capabilities는 이 메서드 전에 이미 처리된다)는
        스킬이 아니라 신뢰하는 코어 자신이므로 manifest 선언 범위를 적용하지 않고 전체
        레지스트리 서비스를 그대로 준다. 스킬 도구는 core/slash.py의 _player_context와 같은
        규칙으로, manifest.services에 선언한 것만 준다(선언하지 않은 서비스/필드는 접근 자체가
        막힌다 — ToolContext.__getattr__ 참고)."""
        if skill_name is None:
            services: Mapping[str, object] = {**self._executor.services, **ctx.services}
            return ToolContext(
                player_id=ctx.player_id,
                tags=ctx.tags,
                services=services,
                guild_id=ctx.guild_id,
                event_bus=ctx.event_bus,
                player_data=ctx.player_data,
                db=ctx.db,
                registry_snapshot=ctx.registry_snapshot,
                usage=ctx.usage,
                discord_id=ctx.discord_id,
            )

        declared = set(declared_services)
        services = {
            name: self._executor.services[name] for name in declared if name in self._executor.services
        }
        return ToolContext(
            player_id=ctx.player_id,
            tags=ctx.tags,
            services=services,
            guild_id=ctx.guild_id,
            event_bus=ctx.event_bus,
            player_data=self._executor.player_data if "player_data" in declared else None,
            db=self._db if "db" in declared else None,
            registry_snapshot=self._registry_snapshot if "registry_snapshot" in declared else None,
            usage=self._executor.services.get("usage") if "usage" in declared else None,  # pyright: ignore[reportArgumentType]
            discord_id=ctx.discord_id,
        )
