from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, get_type_hints

logger = logging.getLogger(__name__)

Stage = Literal["great", "good", "close", "rest"]

_chain_depth: contextvars.ContextVar[int] = contextvars.ContextVar("yuni_event_chain_depth", default=0)


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
    fn: Callable[[Any], Awaitable[Any]]


class EventBus:
    def __init__(self, *, gather_timeout: float = 5.0, max_chain_depth: int = 3) -> None:
        self._gather_timeout = gather_timeout
        self._max_chain_depth = max_chain_depth
        self._notify_handlers: dict[str, list[_Handler]] = {}
        self._gather_handlers: dict[str, list[_Handler]] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def register_notify(self, event_name: str, *, owner_skill: str, fn: Callable[[Any], Awaitable[Any]]) -> None:
        self._notify_handlers.setdefault(event_name, []).append(_Handler(owner_skill=owner_skill, fn=fn))

    def register_gather(self, event_name: str, *, owner_skill: str, fn: Callable[[Any], Awaitable[Any]]) -> None:
        self._gather_handlers.setdefault(event_name, []).append(_Handler(owner_skill=owner_skill, fn=fn))

    async def emit_notify(self, event_name: str, payload: Any) -> None:
        if _chain_depth.get() > self._max_chain_depth:
            logger.warning("event chain depth exceeded, dropping %s", event_name)
            return

        for handler in self._notify_handlers.get(event_name, []):
            task = asyncio.create_task(self._run_notify_handler(handler, payload))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _run_notify_handler(self, handler: _Handler, payload: Any) -> None:
        token = _chain_depth.set(_chain_depth.get() + 1)
        try:
            await handler.fn(payload)
        except Exception:
            logger.exception("notify handler '%s' failed for owner %s", handler.fn, handler.owner_skill)
        finally:
            _chain_depth.reset(token)

    async def emit_gather(self, event_name: str, payload: Any) -> list[Section]:
        handlers = self._gather_handlers.get(event_name, [])
        if not handlers:
            return []

        token = _chain_depth.set(_chain_depth.get() + 1)
        try:
            results = await asyncio.gather(
                *(self._run_gather_handler(h, payload) for h in handlers), return_exceptions=False
            )
        finally:
            _chain_depth.reset(token)

        sections = [(owner, section) for owner, section in results if section is not None]
        sections.sort(key=lambda pair: (pair[1].order, pair[0]))
        return [section for _, section in sections]

    async def _run_gather_handler(self, handler: _Handler, payload: Any) -> tuple[str, Section | None]:
        try:
            section = await asyncio.wait_for(handler.fn(payload), timeout=self._gather_timeout)
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


def handler_kind(fn: Callable[[Any], Awaitable[Any]]) -> Literal["notify", "gather"]:
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
