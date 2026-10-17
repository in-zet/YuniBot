from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

ConfirmMode = Literal["never", "bulk", "always"]
CostTier = Literal["light", "network", "llm"]


class DecoratorError(ValueError):
    pass


@dataclass(slots=True, kw_only=True)
class ToolSpec:
    fn: Callable[..., Awaitable[Any]]
    name: str
    slash: str | None = None
    tags: list[str] = field(default_factory=list)
    tags_any: list[str] = field(default_factory=list)
    mutates: bool = False
    confirm: ConfirmMode = "never"
    cost: CostTier = "light"


@dataclass(slots=True, kw_only=True)
class SlashOnlySpec:
    fn: Callable[..., Awaitable[Any]]
    name: str
    slash: str
    tags: list[str] = field(default_factory=list)
    tags_any: list[str] = field(default_factory=list)
    mutates: bool = False
    confirm: ConfirmMode = "never"


@dataclass(slots=True, kw_only=True)
class EventHandlerSpec:
    fn: Callable[..., Awaitable[Any]]
    event_name: str
    tags: list[str] = field(default_factory=list)
    tags_any: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class ScheduleSpec:
    fn: Callable[..., Awaitable[Any]]
    cron: str
    catch_up: bool = False


@dataclass(slots=True, kw_only=True)
class PromptContextSpec:
    fn: Callable[..., Awaitable[Any]]
    max_chars: int = 300


@dataclass(slots=True, kw_only=True)
class SkillCode:
    tools: list[ToolSpec] = field(default_factory=list)
    slash_only_tools: list[SlashOnlySpec] = field(default_factory=list)
    event_handlers: list[EventHandlerSpec] = field(default_factory=list)
    schedules: list[ScheduleSpec] = field(default_factory=list)
    prompt_contexts: list[PromptContextSpec] = field(default_factory=list)


def _require_docstring(fn: Callable[..., Any]) -> None:
    if not (fn.__doc__ and fn.__doc__.strip()):
        raise DecoratorError(f"'{fn.__name__}'에 docstring이 없다. 도구 설명은 docstring에서 나온다")


def _require_async(fn: Callable[..., Any]) -> None:
    if not inspect.iscoroutinefunction(fn):
        raise DecoratorError(f"'{fn.__name__}'은 async 함수여야 한다")


def _require_tool_context_first_param(fn: Callable[..., Any]) -> None:
    params = list(inspect.signature(fn).parameters.values())
    if not params or params[0].name != "ctx":
        raise DecoratorError(
            f"'{fn.__name__}'의 첫 인자는 ToolContext 타입의 'ctx'여야 한다"
        )


def tool(
    *,
    slash: str | None = None,
    tags: list[str] | None = None,
    tags_any: list[str] | None = None,
    mutates: bool = False,
    confirm: ConfirmMode = "never",
    cost: CostTier = "light",
) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        _require_async(fn)
        _require_docstring(fn)
        _require_tool_context_first_param(fn)
        fn.__yuni_tool__ = ToolSpec(  # pyright: ignore[reportFunctionMemberAccess]
            fn=fn,
            name=fn.__name__,
            slash=slash,
            tags=tags or [],
            tags_any=tags_any or [],
            mutates=mutates,
            confirm=confirm,
            cost=cost,
        )
        return fn

    return deco


def slash_only(
    slash: str,
    *,
    tags: list[str] | None = None,
    tags_any: list[str] | None = None,
    mutates: bool = False,
    confirm: ConfirmMode = "never",
) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        _require_async(fn)
        _require_docstring(fn)
        _require_tool_context_first_param(fn)
        fn.__yuni_slash_only__ = SlashOnlySpec(  # pyright: ignore[reportFunctionMemberAccess]
            fn=fn,
            name=fn.__name__,
            slash=slash,
            tags=tags or [],
            tags_any=tags_any or [],
            mutates=mutates,
            confirm=confirm,
        )
        return fn

    return deco


def on(
    event_name: str, *, tags: list[str] | None = None, tags_any: list[str] | None = None
) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        _require_async(fn)
        _require_tool_context_first_param(fn)
        fn.__yuni_on__ = EventHandlerSpec(  # pyright: ignore[reportFunctionMemberAccess]
            fn=fn, event_name=event_name, tags=tags or [], tags_any=tags_any or []
        )
        return fn

    return deco


def schedule(cron: str, *, catch_up: bool = False) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        _require_async(fn)
        _require_tool_context_first_param(fn)
        fn.__yuni_schedule__ = ScheduleSpec(fn=fn, cron=cron, catch_up=catch_up)  # pyright: ignore[reportFunctionMemberAccess]
        return fn

    return deco


def prompt_context(*, max_chars: int = 300) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        _require_async(fn)
        _require_tool_context_first_param(fn)
        fn.__yuni_prompt_context__ = PromptContextSpec(fn=fn, max_chars=max_chars)  # pyright: ignore[reportFunctionMemberAccess]
        return fn

    return deco


def collect_specs(module: Any) -> SkillCode:
    """import된 skill.py 모듈에서 장식된 함수를 모두 모은다."""
    code = SkillCode()
    for _, member in inspect.getmembers(module, inspect.iscoroutinefunction):
        if spec := getattr(member, "__yuni_tool__", None):
            code.tools.append(spec)
        if spec := getattr(member, "__yuni_slash_only__", None):
            code.slash_only_tools.append(spec)
        if spec := getattr(member, "__yuni_on__", None):
            code.event_handlers.append(spec)
        if spec := getattr(member, "__yuni_schedule__", None):
            code.schedules.append(spec)
        if spec := getattr(member, "__yuni_prompt_context__", None):
            code.prompt_contexts.append(spec)
    return code
