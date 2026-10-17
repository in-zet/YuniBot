# core/executor.py
from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.decorators import DRY_RUN_PARAM, SlashOnlySpec, ToolSpec
from core.events import EventBus
from core.manifest import DefinedTag
from core.permissions import (
    SkillToggleProvider,
    TagProvider,
    TagRequirement,
    check_tags,
    describe_lock,
)
from core.player_data import PlayerDataScanner
from core.resolvers import ResolutionError, ResolverRegistry
from core.schema import build_arg_schema
from core.tool_types import ToolContext, ToolResult

logger = logging.getLogger(__name__)


@dataclass(slots=True, kw_only=True)
class ExecutionOutcome:
    result: ToolResult | None
    needs_confirm: bool = False
    confirm_token: str | None = None
    # confirm="bulk" 도구가 dry_run으로 돌려준 미리보기(바뀔 건수와 안내문). 확인 버튼과
    # 함께 보여준다. confirm="always"면 None이다.
    preview: ToolResult | None = None


def _bulk_count(preview: ToolResult) -> int:
    count = preview.data.get("count", 0)
    return count if isinstance(count, int) else 0


@dataclass(slots=True, kw_only=True)
class _PendingConfirm:
    spec: ToolSpec | SlashOnlySpec
    ctx: ToolContext
    resolved_args: dict[str, Any]
    requester_id: str  # 확인을 요청한 플레이어. 이 사람만 확인/취소할 수 있다.


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
        services: Mapping[str, object] | None = None,
        event_bus: EventBus | None = None,
        player_data: PlayerDataScanner | None = None,
        settings: SkillToggleProvider | None = None,
    ) -> None:
        self._resolvers = resolvers
        self._tag_provider = tag_provider
        self._tag_definitions = tag_definitions
        self._services = services or {}
        self._event_bus = event_bus or EventBus()
        self._player_data = player_data
        self._settings = settings
        self._pending: dict[str, _PendingConfirm] = {}

    @property
    def tag_provider(self) -> TagProvider:
        """슬래시 콜백(core/slash.py)이 실행기와 같은 태그 제공자를 공유하기 위해 쓴다."""
        return self._tag_provider

    @property
    def services(self) -> Mapping[str, object]:
        """등록된 모든 공통/게임 서비스. 슬래시 콜백이 스킬별로 걸러 ToolContext에 싣는다."""
        return self._services

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def player_data(self) -> PlayerDataScanner | None:
        return self._player_data

    @property
    def settings(self) -> SkillToggleProvider | None:
        """core/slash.py의 모달 콜백이 일반 _dispatch와 같은 "스킬 꺼짐" 검사를 하기 위해 쓴다."""
        return self._settings

    async def invoke(
        self,
        spec: ToolSpec | SlashOnlySpec,
        ctx: ToolContext,
        raw_args: dict[str, Any],
        *,
        skill_tags: list[str] | None = None,
        skill_tags_any: list[str] | None = None,
        skill_name: str | None = None,
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
        if (
            skill_name is not None
            and ctx.guild_id is not None
            and self._settings is not None
            and not await self._settings.is_skill_enabled(guild_id=ctx.guild_id, skill=skill_name)
        ):
            return ExecutionOutcome(result=ToolResult(error="모르는 명령이에요"))

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
                # 세 경우를 구분한다. 어느 쪽이든 날것 예외를 호출자(슬래시/LLM 루프)에 흘려보내지 않는다.
                # 1) 해석기 미등록: get()/resolve()를 부르기 전에 확인해 바로 에러 결과로.
                # 2) ResolutionError: 해석기의 문서화된 실패(찾지 못함/후보 여럿) — 그대로 안내.
                # 3) 그 밖의 예외: 해석기 자신의 버그다. 1)과 섞여 사라지지 않도록 로그를 남긴다.
                if not self._resolvers.is_registered(arg.py_type):
                    return ExecutionOutcome(
                        result=ToolResult(error=f"{arg.py_type.__name__}에 등록된 해석기가 없다")
                    )
                resolver = self._resolvers.get(arg.py_type)
                try:
                    resolved[arg.name] = await resolver.resolve(raw_value, ctx)
                except ResolutionError as exc:
                    return ExecutionOutcome(result=ToolResult(error=str(exc)))
                except Exception as exc:
                    logger.exception(
                        "resolver for %s raised unexpectedly (tool '%s', arg '%s')",
                        arg.py_type.__name__,
                        spec.name,
                        arg.name,
                    )
                    return ExecutionOutcome(
                        result=ToolResult(error=f"인자 '{arg.name}' 해석 중 오류: {exc}")
                    )
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
            return self._hold(spec, ctx, resolved)

        if confirm_mode == "bulk":
            # 먼저 dry_run=True로 불러 바뀔 건수(data["count"])만 받는다 — 이 호출은 아무것도
            # 쓰지 않는다(도구의 약속, @tool이 dry_run 인자를 강제한다). 2건 이상이면 확인을
            # 받고, 0~1건(또는 count가 없음)이면 확인 없이 바로 실제로 부른다.
            preview = await self._call(spec, ctx, {**resolved, DRY_RUN_PARAM: True})
            if preview.error is not None:
                return ExecutionOutcome(result=preview)
            if _bulk_count(preview) > 1:
                return self._hold(spec, ctx, resolved, preview=preview)

        return ExecutionOutcome(result=await self._call(spec, ctx, resolved))

    def _hold(
        self,
        spec: ToolSpec | SlashOnlySpec,
        ctx: ToolContext,
        resolved: dict[str, Any],
        *,
        preview: ToolResult | None = None,
    ) -> ExecutionOutcome:
        """확인을 받을 때까지 실행을 미룬다. resolved에는 dry_run이 들어가지 않는다 —
        확인 뒤 confirm()이 그대로 실제 호출에 쓴다."""
        token = str(uuid.uuid4())
        self._pending[token] = _PendingConfirm(
            spec=spec, ctx=ctx, resolved_args=resolved, requester_id=ctx.player_id
        )
        return ExecutionOutcome(result=None, needs_confirm=True, confirm_token=token, preview=preview)

    async def confirm(self, confirm_token: str, *, confirmed: bool, player_id: str) -> ToolResult:
        """player_id는 버튼을 누른 플레이어다. 요청한 본인이 아니면 실행도 취소도 하지 않고,
        토큰도 소비하지 않는다(본인은 그대로 확인/취소할 수 있다)."""
        pending = self._pending.get(confirm_token)
        if pending is None:
            return ToolResult(error="만료되었거나 모르는 확인 요청이다")
        if pending.requester_id != player_id:
            logger.warning(
                "confirm token for '%s' pressed by another player (requester=%s, presser=%s)",
                pending.spec.name,
                pending.requester_id,
                player_id,
            )
            return ToolResult(error="본인이 요청한 확인만 누를 수 있다")
        del self._pending[confirm_token]
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
