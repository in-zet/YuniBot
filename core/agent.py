# core/agent.py
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from core.core_tools import CORE_TOOL_SPECS, THREAD_TOOL_SPECS
from core.decorators import ToolSpec
from core.executor import ToolExecutor
from core.llm import (
    FunctionResultPart,
    LlmAdapter,
    LlmContent,
    LlmPart,
    OnRetry,
    UsageInfo,
)
from core.prompt import PromptAssembler
from core.registry import LoadedSkill
from core.registry_snapshot import RegistrySnapshot
from core.skill_loading import SkillLoadState
from core.tables import WorkTable, summarize_table
from core.threads import ThreadEndRequest
from core.tool_types import Display, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_CORE_TOOLS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in [*CORE_TOOL_SPECS, *THREAD_TOOL_SPECS]}

# 이번 턴에서 heavy 스킬(manifest.weight == "heavy")의 도구를 처음 부르기 직전에 한 번
# 불린다 — core/mention.py가 이걸로 "확인하고 있다"를 시간 기다리지 않고 바로 보낸다.
OnHeavyCall = Callable[[], Awaitable[None]]


@dataclass(slots=True, kw_only=True)
class AgentTurnResult:
    text: str | None = None
    collected_data: list[dict[str, Any]] = field(default_factory=list)
    optional_displays: dict[str, Display] = field(default_factory=dict)
    required_displays: list[Display] = field(default_factory=list)
    usage: UsageInfo = field(default_factory=lambda: UsageInfo(input_tokens=0, output_tokens=0))
    hit_tool_call_cap: bool = False
    hit_token_cap: bool = False
    # admin 태그가 필요한 도구를 부른 턴 — 관리 대화는 플레이어 메모리에 남기지 않는다.
    used_admin_tool: bool = False
    # 마지막 LLM 왕복에서 실제로 답한 모델. 보조 모델로 넘어간 턴이면 그 이름이고, 아니면 None.
    served_model: str | None = None
    # 이번 턴에 만든 표 전부(보관소 id). 번호(`rows`·`"t3:2"`)는 사람이 본 표에만 쓰므로 이 표들에는 거절한다.
    turn_table_ids: list[str] = field(default_factory=list)
    # 이번 턴의 마지막 표. 턴 끝에 이 표 하나만 글자로 그린다.
    last_table_id: str | None = None
    # 그림 표 도구가 이미 그린 표. 글자로 다시 그리지 않는다.
    drawn_table_ids: list[str] = field(default_factory=list)
    # 마지막 표 본체와 옛 값 여부. 턴 끝 글자 출력(core/response_assembly.py)이 쓴다.
    last_table: WorkTable | None = None
    last_table_stale: bool = False


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

    def _requires_admin(self, spec: ToolSpec, skill_name: str | None) -> bool:
        if "admin" in spec.tags or "admin" in spec.tags_any:
            return True
        if skill_name is None:
            return False
        return any(
            skill.manifest.name == skill_name and ("admin" in skill.manifest.tags or "admin" in skill.manifest.tags_any)
            for skill in self._registry_skills()
        )

    def _max_calls_for(self, tool_name: str) -> int | None:
        found = self._find_spec(tool_name)
        return found[0].max_calls_per_turn if found is not None else None

    def _skill_weight(self, skill_name: str | None) -> str:
        if skill_name is None:
            return "light"  # core 도구(open_skill/remember 등)는 네트워크 스크래핑이 없다
        for skill in self._registry_skills():
            if skill.manifest.name == skill_name:
                return skill.manifest.weight
        return "light"

    def _skills_used(self, tool_names: list[str], player_tags: set[str]) -> list[str]:
        """불린 도구 이름('<스킬>_<함수>')에서 쓰인 스킬을 순서대로 중복 없이 뽑는다(로그 전용 — 사전 펼침을
        설계할 근거 데이터). 코어 도구(open_skill 등)는 스킬이 없어 건너뛴다."""
        skills = [card.manifest.name for card in self._prompt.visible_skill_cards(player_tags=player_tags)]
        used: dict[str, None] = {}
        for name in tool_names:
            skill = next((s for s in skills if name.startswith(f"{s}_")), None)
            if skill is not None:
                used[skill] = None
        return list(used)

    async def _skill_prompt_snippets(self, ctx: ToolContext) -> list[str]:
        """스킬의 `@prompt_context`가 내는 짧은 맥락(예: 열린 약속)을 모은다. 스킬이 펼쳐지지 않아도 들어가고,
        스킬당 `max_chars`를 넘는 부분은 자른다. 한 스킬이 실패해도 응답은 막지 않는다."""
        snippets: list[str] = []
        settings = self._executor.settings
        for skill in self._prompt.visible_skill_cards(player_tags=set(ctx.tags)):
            if not skill.code.prompt_contexts:
                continue
            if (
                ctx.guild_id is not None and settings is not None
                and not await settings.is_skill_enabled(guild_id=ctx.guild_id, skill=skill.manifest.name)
            ):
                continue
            scoped = self._scoped_ctx(
                ctx, skill_name=skill.manifest.name, declared_services=list(skill.manifest.services)
            )
            for spec in skill.code.prompt_contexts:
                try:
                    text = await spec.fn(scoped)
                except Exception:
                    logger.warning("스킬 '%s'의 프롬프트 맥락 실패, 건너뛴다", skill.manifest.name, exc_info=True)
                    continue
                if isinstance(text, str) and text.strip():
                    snippets.append(text.strip()[: spec.max_chars])
        return snippets

    async def run(
        self,
        *,
        ctx: ToolContext,
        skill_state: SkillLoadState,
        user_text: str,
        prompt_context_snippets: list[str],
        on_retry: OnRetry | None = None,
        on_heavy_call: OnHeavyCall | None = None,
        thread_end: ThreadEndRequest | None = None,
    ) -> AgentTurnResult:
        """thread_end가 있으면 대화 스레드 안의 응답이다 — 모델에게 end_conversation 도구가 보이고, 부르면 thread_end가 켜진다."""
        result = AgentTurnResult()
        # 코어 도구 전용 내부 키(manifest.services 선언 대상이 아니다): table_query/table_peek이 쓴다.
        services: dict[str, object] = {
            **ctx.services,
            "_skill_load_state": skill_state,
            "_turn_table_ids": result.turn_table_ids,
            "_ref_resolvers": self._executor.resolvers,
        }
        if thread_end is not None:
            services["_thread_end"] = thread_end
        inner_ctx = ToolContext(
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
            scheduler=ctx.scheduler,
            tables=ctx.tables,
            channel_id=ctx.channel_id,
        )

        prompt_context_snippets = [*prompt_context_snippets, *await self._skill_prompt_snippets(ctx)]
        contents: list[LlmContent] = [LlmContent(role="user", parts=[LlmPart(text=user_text)])]
        tool_calls_made = 0
        tool_call_log: list[str] = []
        tool_call_counts: dict[str, int] = {}
        llm_turns = 0
        heavy_notified = False

        while True:
            llm_turns += 1
            player_tags = set(ctx.tags)
            system_instruction = self._prompt.build_system_instruction(
                player_tags=player_tags,
                skill_state=skill_state,
                prompt_context_snippets=prompt_context_snippets,
            )
            tools = self._prompt.build_tool_declarations(
                player_tags=player_tags, skill_state=skill_state, in_thread=thread_end is not None
            )

            llm_started = time.monotonic()
            turn = await self._llm.generate(
                model=self._model,
                system_instruction=system_instruction,
                contents=contents,
                tools=tools,
                thinking_level=self._thinking_level,
                on_retry=on_retry,
            )
            logger.info(
                "player %s: LLM 왕복 %d회차 %.2f초 (함수 호출 %d개, 토큰 입력 %d(캐시 %d)/출력 %d)",
                ctx.player_id, llm_turns, time.monotonic() - llm_started, len(turn.function_calls),
                turn.usage.input_tokens, turn.usage.cached_input_tokens, turn.usage.output_tokens,
            )
            result.served_model = turn.model
            result.usage = UsageInfo(
                input_tokens=result.usage.input_tokens + turn.usage.input_tokens,
                output_tokens=result.usage.output_tokens + turn.usage.output_tokens,
                cached_input_tokens=result.usage.cached_input_tokens + turn.usage.cached_input_tokens,
            )
            if result.usage.input_tokens + result.usage.output_tokens > self._max_tokens:
                logger.warning(
                    "player %s: 토큰 상한(%d) 초과(누적 입력 %d/출력 %d) — LLM 턴 %d회, 불린 도구 %s, 쓴 스킬 %s",
                    ctx.player_id, self._max_tokens, result.usage.input_tokens, result.usage.output_tokens,
                    llm_turns, tool_call_log, self._skills_used(tool_call_log, player_tags),
                )
                result.hit_token_cap = True
                result.text = turn.text
                return result

            if not turn.function_calls:
                logger.info(
                    "player %s: 멘션 응답 종료 — LLM 턴 %d회, 도구 호출 %s, 쓴 스킬 %s, 누적 토큰 입력 %d(캐시 %d)/출력 %d",
                    ctx.player_id, llm_turns, tool_call_log, self._skills_used(tool_call_log, player_tags),
                    result.usage.input_tokens, result.usage.cached_input_tokens, result.usage.output_tokens,
                )
                result.text = turn.text
                return result

            # 모델 자신의 턴은 raw(제공자 Content, thought signature 포함)를 그대로 보존해야
            # 하므로 turn.content를 그대로 append한다 — LlmContent를 새로 만들지 않는다.
            contents.append(turn.content)

            response_parts: list[LlmPart] = []
            for call in turn.function_calls:
                tool_calls_made += 1
                if tool_calls_made > self._max_tool_calls:
                    logger.warning(
                        "player %s: 도구 호출 상한(%d) 초과 — LLM 턴 %d회, 불린 도구 %s, 넘긴 호출 '%s'",
                        ctx.player_id, self._max_tool_calls, llm_turns, tool_call_log, call.name,
                    )
                    result.hit_tool_call_cap = True
                    result.text = turn.text
                    return result

                tool_call_log.append(call.name)
                logger.info(
                    "player %s: 도구 호출 %d/%d '%s' args=%s",
                    ctx.player_id, tool_calls_made, self._max_tool_calls, call.name, call.args,
                )
                found = self._find_spec(call.name)
                if found is not None and found[1] is not None:
                    skill_state.touch(found[1])
                if found is not None and self._requires_admin(found[0], found[1]):
                    result.used_admin_tool = True
                if (
                    not heavy_notified and on_heavy_call is not None
                    and found is not None and self._skill_weight(found[1]) == "heavy"
                ):
                    heavy_notified = True
                    await on_heavy_call()

                tool_call_counts[call.name] = tool_call_counts.get(call.name, 0) + 1
                per_tool_cap = self._max_calls_for(call.name)
                if per_tool_cap is not None and tool_call_counts[call.name] > per_tool_cap:
                    # 전역 상한과 달리 이 호출 하나만 거절한다 — 턴은 멈추지 않고 모델이
                    # 다른 도구나 지금까지 모은 결과로 답을 이어 가게 둔다.
                    logger.warning(
                        "player %s: 도구 '%s' 응답당 호출 상한(%d) 초과 — 이 호출만 거절",
                        ctx.player_id, call.name, per_tool_cap,
                    )
                    tool_result = ToolResult(
                        error=f"'{call.name}'은 이번 응답에서 이미 {per_tool_cap}번 불렀다. 더 부르지 마라"
                    )
                else:
                    tool_started = time.monotonic()
                    tool_result = await self._invoke(
                        inner_ctx, call.name, call.args, turn_table_ids=result.turn_table_ids
                    )
                    if tool_result.table is not None and tool_result.error is None:
                        tool_result = self._keep_table(inner_ctx, tool_result, result)
                    logger.info(
                        "player %s: 도구 '%s' 완료 %.2f초",
                        ctx.player_id, call.name, time.monotonic() - tool_started,
                    )
                if tool_result.drew_table is not None and tool_result.error is None:
                    result.drawn_table_ids.append(tool_result.drew_table)
                if tool_result.data:
                    result.collected_data.append(tool_result.data)
                if tool_result.display is not None and tool_result.display_mode == "optional":
                    display_id = f"d{len(result.optional_displays) + 1}"
                    result.optional_displays[display_id] = tool_result.display
                    tool_result.data["_display_id"] = display_id
                elif tool_result.display is not None and tool_result.display_mode == "required":
                    # required는 00-core.md의 기본 표시 모드다 — 숫자 위반 검사와 무관하게
                    # 항상 붙는다(assemble_response가 이 리스트를 조건 없이 그대로 포함한다).
                    # 목록은 표로 나오고, 턴의 마지막 표 하나만 붙으므로 같은 도구를 또 불러도 겹치지 않는다.
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

    def _keep_table(self, ctx: ToolContext, tool_result: ToolResult, turn: AgentTurnResult) -> ToolResult:
        """도구가 돌려준 표를 보관소에 넣고, 모델에게는 표 요약만 돌려준다."""
        store = self._executor.tables
        table = tool_result.table
        assert table is not None
        if store is None or ctx.channel_id is None:
            return ToolResult(error="이 자리에서는 표를 보관할 수 없다")
        table_id = store.put(ctx.player_id, ctx.channel_id, table, stale=tool_result.table_stale)
        turn.turn_table_ids.append(table_id)
        turn.last_table_id = table_id
        stored = store.get(ctx.player_id, ctx.channel_id, table_id)
        assert stored is not None
        turn.last_table = stored
        turn.last_table_stale = tool_result.table_stale
        tool_result.data = {**tool_result.data, **summarize_table(stored, stale=tool_result.table_stale)}
        return tool_result

    async def _invoke(
        self, ctx: ToolContext, tool_name: str, args: dict[str, Any], *, turn_table_ids: Collection[str] = ()
    ) -> ToolResult:
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
            turn_table_ids=turn_table_ids,
        )
        if outcome.needs_confirm:
            # 멘션 경로에는 확인 버튼을 낼 인터랙션이 없다(대화 스레드 중이라 상호작용
            # 컨텍스트가 없다). 슬래시처럼 버튼으로 다시 묻는 대신, 플레이어가 이미 멘션으로
            # 명시적으로 요청한 것 자체를 확인으로 보고 바로 실행한다(2026-10-18 사용자 결정).
            assert outcome.confirm_token is not None
            return await self._executor.confirm(
                outcome.confirm_token, confirmed=True, player_id=scoped_ctx.player_id
            )
        assert outcome.result is not None
        outcome.result.table_stale = outcome.stale_input
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
                scheduler=ctx.scheduler,
                tables=self._executor.tables,
                channel_id=ctx.channel_id,
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
            scheduler=self._executor.scheduler if "scheduler" in declared else None,
            tables=self._executor.tables if "tables" in declared else None,
            channel_id=ctx.channel_id,
        )
