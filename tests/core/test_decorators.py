import pytest

from core.decorators import (
    DecoratorError,
    collect_specs,
    on,
    prompt_context,
    schedule,
    slash_only,
    tool,
)
from core.tool_types import ToolContext, ToolResult


@tool(slash="보더", tags=["chunithm-synced"], cost="network")
async def lookup(ctx: ToolContext, name: str) -> ToolResult:
    """곡과 난이도의 허용 어택 개수를 계산한다."""
    return ToolResult(data={"name": name})


@on("chunithm.sync.completed")
async def on_sync(ctx: ToolContext, payload: dict[str, object]) -> None:
    """동기화 완료 이벤트를 받는다."""


@schedule("0 9 * * *", catch_up=True)
async def daily(ctx: ToolContext) -> None:
    """매일 9시에 돈다."""


@prompt_context(max_chars=200)
async def hint(ctx: ToolContext) -> str:
    """플레이어 맥락을 짧게 돌려준다."""
    return "맥락"


@slash_only("연습 체험시작", mutates=True)
async def start_demo(ctx: ToolContext) -> ToolResult:
    """데모 상태 태그를 부여한다."""
    return ToolResult(data={})


@on("chunithm.login.expired", tags=["chunithm-linked"], tags_any=["chunithm-synced", "chunithm-vip"])
async def on_login_expired_tagged(ctx: ToolContext, payload: dict[str, object]) -> None:
    """태그 조건이 붙은 알림 핸들러."""


def test_on_handler_spec_defaults_to_empty_tags() -> None:
    spec = on_sync.__yuni_on__  # pyright: ignore[reportFunctionMemberAccess]
    assert spec.event_name == "chunithm.sync.completed"
    assert spec.tags == []
    assert spec.tags_any == []


def test_on_handler_spec_carries_tags() -> None:
    spec = on_login_expired_tagged.__yuni_on__  # pyright: ignore[reportFunctionMemberAccess]
    assert spec.tags == ["chunithm-linked"]
    assert spec.tags_any == ["chunithm-synced", "chunithm-vip"]


def test_tool_spec_attached() -> None:
    spec = lookup.__yuni_tool__  # pyright: ignore[reportFunctionMemberAccess]
    assert spec.slash == "보더"
    assert spec.tags == ["chunithm-synced"]
    assert spec.cost == "network"
    assert spec.confirm == "never"
    assert spec.mutates is False


def test_collect_specs_groups_everything() -> None:
    import types

    module = types.ModuleType("fake_skill")
    module.__dict__.update(
        lookup=lookup,
        on_sync=on_sync,
        daily=daily,
        hint=hint,
        start_demo=start_demo,
    )

    code = collect_specs(module)

    assert [spec.fn.__name__ for spec in code.tools] == ["lookup"]
    assert [spec.event_name for spec in code.event_handlers] == ["chunithm.sync.completed"]
    assert [spec.cron for spec in code.schedules] == ["0 9 * * *"]
    assert code.schedules[0].catch_up is True
    assert [spec.max_chars for spec in code.prompt_contexts] == [200]
    assert [spec.slash for spec in code.slash_only_tools] == ["연습 체험시작"]


def test_collect_specs_ignores_bystanders() -> None:
    import types

    def helper(x: int) -> int:
        return x

    async def plain_async(ctx: ToolContext) -> None:
        return None

    module = types.ModuleType("noisy_skill")
    module.__dict__.update(
        lookup=lookup,
        on_sync=on_sync,
        daily=daily,
        hint=hint,
        start_demo=start_demo,
        helper=helper,
        plain_async=plain_async,
        VERSION=3,
        TABLE={"a": 1},
    )

    code = collect_specs(module)

    assert [spec.fn.__name__ for spec in code.tools] == ["lookup"]
    assert [spec.fn.__name__ for spec in code.event_handlers] == ["on_sync"]
    assert [spec.fn.__name__ for spec in code.schedules] == ["daily"]
    assert [spec.fn.__name__ for spec in code.prompt_contexts] == ["hint"]
    assert [spec.fn.__name__ for spec in code.slash_only_tools] == ["start_demo"]


def test_tool_requires_async_function() -> None:
    with pytest.raises(DecoratorError, match="async"):

        @tool(slash="실패3")  # pyright: ignore[reportArgumentType]
        def sync_fn(ctx: ToolContext) -> ToolResult:
            """동기 함수."""
            return ToolResult(data={})


def test_tool_requires_docstring() -> None:
    with pytest.raises(DecoratorError, match="docstring"):

        @tool(slash="실패")
        async def no_doc(ctx: ToolContext) -> ToolResult:
            return ToolResult(data={})


def test_tool_requires_tool_context_first_param() -> None:
    with pytest.raises(DecoratorError, match="ToolContext"):

        @tool(slash="실패2")
        async def bad_first_param(name: str) -> ToolResult:
            """첫 인자가 ctx가 아님."""
            return ToolResult(data={})


def test_bulk_confirm_tool_requires_dry_run_param() -> None:
    with pytest.raises(DecoratorError, match="dry_run"):

        @tool(slash="일괄", mutates=True, confirm="bulk")
        async def no_dry_run(ctx: ToolContext, n: int) -> ToolResult:
            """dry_run이 없는 bulk 도구."""
            return ToolResult(data={"count": n})


def test_bulk_confirm_tool_with_dry_run_param_is_accepted() -> None:
    @tool(slash="일괄", mutates=True, confirm="bulk")
    async def with_dry_run(ctx: ToolContext, n: int, *, dry_run: bool = False) -> ToolResult:
        """dry_run이 있는 bulk 도구."""
        return ToolResult(data={"count": n})

    assert with_dry_run.__yuni_tool__.confirm == "bulk"  # pyright: ignore[reportFunctionMemberAccess]
