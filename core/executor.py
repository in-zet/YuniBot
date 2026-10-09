# core/executor.py
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from core.decorators import SlashOnlySpec, ToolSpec
from core.manifest import DefinedTag
from core.permissions import TagProvider, TagRequirement, check_tags, describe_lock
from core.resolvers import ResolutionError, ResolverRegistry
from core.schema import build_arg_schema
from core.tool_types import ToolContext, ToolResult

logger = logging.getLogger(__name__)


@dataclass(slots=True, kw_only=True)
class ExecutionOutcome:
    result: ToolResult | None
    needs_confirm: bool = False
    confirm_token: str | None = None


@dataclass(slots=True, kw_only=True)
class _PendingConfirm:
    spec: ToolSpec | SlashOnlySpec
    ctx: ToolContext
    resolved_args: dict[str, Any]


def _missing_tags(requirement: TagRequirement, player_tags: set[str]) -> set[str]:
    """requirement를 만족하지 못했을 때, 안내에 쓸 "빠진 태그" 이름들을 모은다."""
    missing = set(requirement.tags) - player_tags
    if requirement.tags_any and not (set(requirement.tags_any) & player_tags):
        missing |= set(requirement.tags_any)
    return missing


class ToolExecutor:
    def __init__(
        self,
        *,
        resolvers: ResolverRegistry,
        tag_provider: TagProvider,
        tag_definitions: dict[str, DefinedTag],
    ) -> None:
        self._resolvers = resolvers
        self._tag_provider = tag_provider
        self._tag_definitions = tag_definitions
        self._pending: dict[str, _PendingConfirm] = {}

    @property
    def tag_provider(self) -> TagProvider:
        """슬래시 콜백(core/slash.py)이 실행기와 같은 태그 제공자를 공유하기 위해 쓴다."""
        return self._tag_provider

    async def invoke(
        self,
        spec: ToolSpec | SlashOnlySpec,
        ctx: ToolContext,
        raw_args: dict[str, Any],
        *,
        skill_tags: list[str] | None = None,
        skill_tags_any: list[str] | None = None,
    ) -> ExecutionOutcome:
        """skill_tags/skill_tags_any는 이 도구가 속한 스킬 manifest의 조건이다.

        도구 자신의 tags/tags_any에 "더해지는" 조건이지만, 독립된 조건으로 더해진다:
        스킬 조건과 도구 조건을 각각 check_tags로 검사해 AND로 묶는다. 둘의
        tags_any를 하나의 리스트로 합쳐 한 번에 검사하지 않는다 — 합치면
        "스킬의 tags_any 중 하나라도 있으면 도구의 tags_any 요구는 건너뛴다"는
        식으로 요구가 약해져 버린다(예: 스킬이 tags_any=[A,B], 도구가
        tags_any=[C,D]일 때 플레이어가 A만 가져도 통과해 버리는 것은 잘못이다.
        (A 또는 B)와 (C 또는 D)를 모두 만족해야 한다).
        """
        schema = build_arg_schema(spec.fn)

        resolved: dict[str, Any] = {}
        for arg in schema.args:
            if arg.name not in raw_args:
                if arg.required:
                    return ExecutionOutcome(result=ToolResult(error=f"필수 인자 '{arg.name}'가 없다"))
                resolved[arg.name] = arg.default
                continue

            raw_value = raw_args[arg.name]
            if arg.is_ref_type:
                try:
                    resolver = self._resolvers.get(arg.py_type)
                    resolved[arg.name] = await resolver.resolve(raw_value, ctx)
                except (ResolutionError, KeyError) as exc:
                    # KeyError: ResolverRegistry.get()이 등록되지 않은 타입에 대해 던진다.
                    # 해석 실패도, 해석기 미등록도 모두 "해석 실패"로 같이 묶어 에러 ToolResult로
                    # 돌려준다. 어느 쪽이든 날것 예외를 호출자(슬래시/LLM 루프)에 흘려보내지 않는다.
                    return ExecutionOutcome(result=ToolResult(error=str(exc)))
            else:
                resolved[arg.name] = raw_value

        tool_requirement = TagRequirement(tags=list(spec.tags), tags_any=list(spec.tags_any))
        skill_requirement = TagRequirement(
            tags=list(skill_tags or []), tags_any=list(skill_tags_any or [])
        )
        player_tags = set(ctx.tags)

        tool_ok = check_tags(tool_requirement, player_tags)
        skill_ok = check_tags(skill_requirement, player_tags)
        if not (tool_ok and skill_ok):
            missing = _missing_tags(tool_requirement, player_tags) | _missing_tags(
                skill_requirement, player_tags
            )
            lock = describe_lock(missing_tags=sorted(missing), tag_definitions=self._tag_definitions)
            if lock.visible and lock.message:
                return ExecutionOutcome(result=ToolResult(error=lock.message))
            return ExecutionOutcome(result=ToolResult(error="모르는 명령이에요"))

        confirm_mode = getattr(spec, "confirm", "never")
        if confirm_mode == "always":
            token = str(uuid.uuid4())
            self._pending[token] = _PendingConfirm(spec=spec, ctx=ctx, resolved_args=resolved)
            return ExecutionOutcome(result=None, needs_confirm=True, confirm_token=token)

        return ExecutionOutcome(result=await self._call(spec, ctx, resolved))

    async def confirm(self, confirm_token: str, *, confirmed: bool) -> ToolResult:
        pending = self._pending.pop(confirm_token, None)
        if pending is None:
            return ToolResult(error="만료되었거나 모르는 확인 요청이다")
        if not confirmed:
            return ToolResult(error="취소했다")
        return await self._call(pending.spec, pending.ctx, pending.resolved_args)

    async def _call(
        self, spec: ToolSpec | SlashOnlySpec, ctx: ToolContext, resolved_args: dict[str, Any]
    ) -> ToolResult:
        try:
            return await spec.fn(ctx, **resolved_args)
        except Exception as exc:
            logger.exception("tool '%s' raised", spec.name)
            return ToolResult(error=f"도구 실행 중 오류: {exc}")
