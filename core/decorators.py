from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

ConfirmMode = Literal["never", "bulk", "always"]
# confirm="bulk" 도구에 실행기(core/executor.py)가 넣는 인자 이름. core/schema.py는 이 이름을
# 스키마에서 뺀다. 이 모듈은 다른 core 모듈을 import하지 않으므로 여기 둔다(순환 import 방지).
DRY_RUN_PARAM = "dry_run"
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


def _require_dry_run_for_bulk(fn: Callable[..., Any], confirm: ConfirmMode) -> None:
    """confirm="bulk"는 실행기가 먼저 dry_run=True로 불러 바뀔 건수를 보고 2건 이상일 때만
    확인 버튼을 낸다(core/executor.py). 그러려면 도구가 dry_run 키워드를 받아야 한다."""
    if confirm != "bulk":
        return
    param = inspect.signature(fn).parameters.get(DRY_RUN_PARAM)
    if param is None or param.default is not False:
        raise DecoratorError(
            f"'{fn.__name__}'는 confirm=\"bulk\"라서 '{DRY_RUN_PARAM}: bool = False' 키워드 인자가 있어야 한다"
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
        _require_dry_run_for_bulk(fn, confirm)
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
        _require_dry_run_for_bulk(fn, confirm)
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
