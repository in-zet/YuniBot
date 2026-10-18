# pyright: reportImportCycles=false
from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, get_type_hints

from core.permissions import (
    SkillToggleProvider,
    TagProvider,
    TagRequirement,
    check_tags,
)

if TYPE_CHECKING:
    # core/tool_types.py가 이미 core/events.py를 최상단에서 import한다(ToolContext가
    # EventBus를 필드 타입으로 쓴다) — 반대 방향을 여기서 최상단에 더하면 순환 import가
    # 난다. 타입 체크 시점에만 이 이름을 쓰고, 실제 참조(_build_handler_ctx 안)는
    # 함수 안 지연 import로 만든다(services/chunithm/chunithm_net.py가 이미 쓰는 패턴).
    # basedpyright는 TYPE_CHECKING 가드와 무관하게 이 두 파일의 import 그래프를 순환으로
    # 본다(reportImportCycles) — 런타임에는 실제로 순환이 없다(여기선 타입만, 실제 참조는
    # 함수 안 지연 import). 설계상 의도된 형태라 아래 한 줄만 억제한다.
    from core.tool_types import ToolContext  # pyright: ignore[reportImportCycles]

logger = logging.getLogger(__name__)

Stage = Literal["great", "good", "close", "rest"]
_STAGE_PRIORITY: list[Stage] = ["great", "good", "close", "rest"]


def highest_stage(stages: Iterable[Stage]) -> Stage | None:
    """여러 조각의 stage 중 가장 높은 단계 하나(great > good > close > rest)를 고른다.
    yuni 서비스의 한마디 선택과 여러 목표를 한 조각에서 판정하는 스킬이 같이 쓴다."""
    found = list(stages)
    return min(found, key=_STAGE_PRIORITY.index) if found else None


_chain_depth: contextvars.ContextVar[int] = contextvars.ContextVar("yuni_event_chain_depth", default=0)

_HandlerFn = Callable[..., Awaitable[Any]]


@dataclass(slots=True, kw_only=True)
class Section:
    order: int = 0
    display: Any | None = None
    data: dict[str, Any] = field(default_factory=dict)
    hint: str | None = None
    stage: Stage | None = None


@dataclass(slots=True, kw_only=True)
class _Handler:
    owner_skill: str
    fn: _HandlerFn
    skill_services: list[str] = field(default_factory=list)
    tags: TagRequirement = field(default_factory=TagRequirement)
    # 핸들러가 속한 스킬의 manifest 레벨 태그 조건. 핸들러 자신의 조건(tags)과 합치지 않고
    # 따로 검사한다 — 각각의 tags_any를 하나로 합치면 "둘 중 하나만 맞아도 통과"로 느슨해진다
    # (core/executor.py의 ToolExecutor.invoke가 tool_requirement/skill_requirement를 따로 보는 이유와 같다).
    skill_tags: TagRequirement = field(default_factory=TagRequirement)


class EventBus:
    def __init__(self, *, gather_timeout: float = 5.0, max_chain_depth: int = 3) -> None:
        self._gather_timeout = gather_timeout
        self._max_chain_depth = max_chain_depth
        self._notify_handlers: dict[str, list[_Handler]] = {}
        self._gather_handlers: dict[str, list[_Handler]] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._services: Mapping[str, object] = {}
        self._tag_provider: TagProvider | None = None
        self._settings: SkillToggleProvider | None = None
        self._db: Any = None
        self._scheduler: Any = None

    def bind_dispatch(
        self,
        *,
        services: Mapping[str, object],
        tag_provider: TagProvider,
        settings: SkillToggleProvider | None,
        db: Any = None,
        scheduler: Any = None,
    ) -> None:
        """build_client()가 services/players/settings_service를 다 만든 뒤 한 번 부른다.

        EventBus 자신은 그보다 먼저 만들어지므로 생성자로는 못 받는다(core/bot.py의 현재
        순서를 바꾸지 않기 위한 늦은 바인딩). services는 dict 참조를 그대로 들고 있는다 —
        build_client()가 이후 그 같은 dict에 notify/yuni/usage/http를 더 채워도(현재
        코드가 이미 이런 식으로 서비스를 늦게 채운다) EventBus가 보는 것도 같은 객체라
        최신 상태를 본다.

        db/scheduler는 core/slash.py의 _dispatch와 같은 이유로 services dict 밖에 있는
        ToolContext 전용 필드다(core/registry.py의 CTX_FIELD_SERVICE_NAMES) — 자기 테이블을
        직접 SQL로 건드리는 스킬(예: chunithm-goals)이나 예약을 거는 스킬이 이벤트 핸들러
        안에서도 그 접근을 그대로 쓰려면, 여기서도 슬래시 경로와 같은 연결을 핸들러의
        manifest.services 선언에 따라 채워 줘야 한다(아래 _build_handler_ctx)."""
        self._services = services
        self._tag_provider = tag_provider
        self._settings = settings
        self._db = db
        self._scheduler = scheduler

    def register_notify(
        self,
        event_name: str,
        *,
        owner_skill: str,
        fn: _HandlerFn,
        skill_services: list[str],
        tags: list[str] | None = None,
        tags_any: list[str] | None = None,
        skill_tags: list[str] | None = None,
        skill_tags_any: list[str] | None = None,
    ) -> None:
        self._notify_handlers.setdefault(event_name, []).append(
            _Handler(
                owner_skill=owner_skill,
                fn=fn,
                skill_services=skill_services,
                tags=TagRequirement(tags=tags or [], tags_any=tags_any or []),
                skill_tags=TagRequirement(tags=skill_tags or [], tags_any=skill_tags_any or []),
            )
        )

    def register_gather(
        self,
        event_name: str,
        *,
        owner_skill: str,
        fn: _HandlerFn,
        skill_services: list[str],
        tags: list[str] | None = None,
        tags_any: list[str] | None = None,
        skill_tags: list[str] | None = None,
        skill_tags_any: list[str] | None = None,
    ) -> None:
        self._gather_handlers.setdefault(event_name, []).append(
            _Handler(
                owner_skill=owner_skill,
                fn=fn,
                skill_services=skill_services,
                tags=TagRequirement(tags=tags or [], tags_any=tags_any or []),
                skill_tags=TagRequirement(tags=skill_tags or [], tags_any=skill_tags_any or []),
            )
        )

    async def _build_handler_ctx(
        self, handler: _Handler, *, player_id: str, guild_id: str | None
    ) -> ToolContext | None:
        if (
            guild_id is not None
            and self._settings is not None
            and not await self._settings.is_skill_enabled(guild_id=guild_id, skill=handler.owner_skill)
        ):
            return None
        player_tags: set[str] = (
            await self._tag_provider.tags_for(player_id) if self._tag_provider is not None else set()
        )
        # 이벤트 대상 플레이어가 핸들러 자신의 조건과 그 스킬의 조건을 둘 다 갖춰야 한다
        # (00-core.md). 어느 한쪽이라도 안 맞으면 건너뛴다 — 모으기는 그 조각만 빠지고 알림은 안 불린다.
        if not check_tags(handler.tags, player_tags) or not check_tags(handler.skill_tags, player_tags):
            return None
        scoped_services = {
            name: self._services[name] for name in handler.skill_services if name in self._services
        }
        from core.tool_types import (
            ToolContext,  # 지연 import — 위 TYPE_CHECKING 주석 참고
        )

        return ToolContext(
            player_id=player_id,
            tags=frozenset(player_tags),
            services=scoped_services,
            guild_id=guild_id,
            event_bus=self,
            db=self._db if "db" in handler.skill_services else None,
            scheduler=self._scheduler if "scheduler" in handler.skill_services else None,
        )

    async def emit_notify(
        self, event_name: str, payload: Any, *, player_id: str, guild_id: str | None
    ) -> None:
        if _chain_depth.get() > self._max_chain_depth:
            logger.warning("event chain depth exceeded, dropping %s", event_name)
            return

        for handler in self._notify_handlers.get(event_name, []):
            task = asyncio.create_task(
                self._run_notify_handler(handler, payload, player_id=player_id, guild_id=guild_id)
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _run_notify_handler(
        self, handler: _Handler, payload: Any, *, player_id: str, guild_id: str | None
    ) -> None:
        token = _chain_depth.set(_chain_depth.get() + 1)
        try:
            ctx = await self._build_handler_ctx(handler, player_id=player_id, guild_id=guild_id)
            if ctx is None:
                return
            await handler.fn(ctx, payload)
        except Exception:
            logger.exception("notify handler '%s' failed for owner %s", handler.fn, handler.owner_skill)
        finally:
            _chain_depth.reset(token)

    async def emit_gather(
        self, event_name: str, payload: Any, *, player_id: str, guild_id: str | None
    ) -> list[Section]:
        handlers = self._gather_handlers.get(event_name, [])
        if not handlers:
            return []

        token = _chain_depth.set(_chain_depth.get() + 1)
        try:
            results = await asyncio.gather(
                *(
                    self._run_gather_handler(h, payload, player_id=player_id, guild_id=guild_id)
                    for h in handlers
                ),
                return_exceptions=False,
            )
        finally:
            _chain_depth.reset(token)

        sections = [(owner, section) for owner, section in results if section is not None]
        sections.sort(key=lambda pair: (pair[1].order, pair[0]))
        return [section for _, section in sections]

    async def _run_gather_handler(
        self, handler: _Handler, payload: Any, *, player_id: str, guild_id: str | None
    ) -> tuple[str, Section | None]:
        try:
            ctx = await self._build_handler_ctx(handler, player_id=player_id, guild_id=guild_id)
            if ctx is None:
                return handler.owner_skill, None
            section = await asyncio.wait_for(handler.fn(ctx, payload), timeout=self._gather_timeout)
        except TimeoutError:
            logger.warning("gather handler '%s' (%s) timed out", handler.fn, handler.owner_skill)
            return handler.owner_skill, None
        except Exception:
            logger.exception("gather handler '%s' (%s) failed", handler.fn, handler.owner_skill)
            return handler.owner_skill, None
        return handler.owner_skill, section

    async def drain(self) -> None:
        """테스트 전용: 배경에서 도는 notify 핸들러가 모두 끝나길 기다린다."""
        while self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)


def handler_kind(fn: _HandlerFn) -> Literal["notify", "gather"]:
    """@on 핸들러의 반환 타입 힌트로 notify/gather를 추론한다.

    @on 데코레이터(core/decorators.py)는 종류를 받지 않는다 — 핸들러가 Section을
    돌려주면(모으기), 아니면 아무것도 안 돌려주면(알림) 그 자체로 의도가 드러나기
    때문에, 스킬 작성자가 매번 kind="gather" 같은 걸 더 적을 필요가 없게 한다.
    """
    hints = get_type_hints(fn)
    ret = hints.get("return")
    if ret is Section:
        return "gather"
    args = getattr(ret, "__args__", ())
    if Section in args:
        return "gather"
    return "notify"
