import discord
import pytest

from core.decorators import tool
from core.slash import SlashPlanError, build_slash_plan
from core.tool_types import ToolContext, ToolResult


@tool(slash="보더")
async def standalone(ctx: ToolContext) -> ToolResult:
    """단독 명령."""
    return ToolResult(data={})


@tool(slash="연결 시작")
async def sub1(ctx: ToolContext) -> ToolResult:
    """2단계 하위 명령."""
    return ToolResult(data={})


@tool(slash="관리 스킬 목록")
async def sub2(ctx: ToolContext) -> ToolResult:
    """3단계(그룹+하위) 명령."""
    return ToolResult(data={})


@tool(slash="조회")
async def lookup(ctx: ToolContext, song: str, count: int = 3) -> ToolResult:
    """인자를 받는 조회 명령."""
    return ToolResult(data={"song": song, "count": count})


class _FakeSkill:
    def __init__(self, name: str) -> None:
        self.manifest = type("M", (), {"name": name, "tags": [], "tags_any": []})()


def test_build_plan_standalone_and_subcommands() -> None:
    plan = build_slash_plan(
        [  # pyright: ignore[reportArgumentType]
            (_FakeSkill("border"), standalone.__yuni_tool__),  # pyright: ignore[reportFunctionMemberAccess]
            (_FakeSkill("account-link"), sub1.__yuni_tool__),  # pyright: ignore[reportFunctionMemberAccess]
            (_FakeSkill("admin"), sub2.__yuni_tool__),  # pyright: ignore[reportFunctionMemberAccess]
        ]
    )

    paths = {tuple(entry.path) for entry in plan.entries}
    assert ("보더",) in paths
    assert ("연결", "시작") in paths
    assert ("관리", "스킬", "목록") in paths


def test_build_plan_rejects_more_than_three_levels() -> None:
    @tool(slash="너무 깊은 명령 경로 넷")
    async def too_deep(ctx: ToolContext) -> ToolResult:
        """4단계 깊이."""
        return ToolResult(data={})

    with pytest.raises(SlashPlanError, match="3단계"):
        _ = build_slash_plan(
            [(_FakeSkill("x"), too_deep.__yuni_tool__)]  # pyright: ignore[reportFunctionMemberAccess, reportArgumentType]
        )


def test_attach_to_tree_builds_offline_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.executor import ToolExecutor
    from core.permissions import InMemoryTagProvider
    from core.resolvers import ResolverRegistry
    from core.slash import attach_to_tree

    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    plan = build_slash_plan(
        [(_FakeSkill("border"), standalone.__yuni_tool__)]  # pyright: ignore[reportFunctionMemberAccess, reportArgumentType]
    )
    executor = ToolExecutor(resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={})

    attach_to_tree(tree, plan, executor=executor)

    assert tree.get_command("보더") is not None


def test_attach_to_tree_builds_real_named_options_for_argument_tool() -> None:
    """brief가 쓴 `**options` + `__annotations__` 덮어쓰기 방식은 discord.py의
    `inspect.signature(callback).parameters` 기반 옵션 추출과 맞지 않는다(실제로
    `app_commands.Command.__init__`이 `TypeError`를 던진다 — VAR_KEYWORD 파라미터는
    명시적으로 지원하지 않는 kind이기 때문이다). 이 테스트는 인자가 있는 도구가 실제로
    이름이 붙은 개별 slash 옵션을 받는지 직접 확인한다."""
    from core.executor import ToolExecutor
    from core.permissions import InMemoryTagProvider
    from core.resolvers import ResolverRegistry
    from core.slash import attach_to_tree

    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    plan = build_slash_plan(
        [(_FakeSkill("songdb"), lookup.__yuni_tool__)]  # pyright: ignore[reportFunctionMemberAccess, reportArgumentType]
    )
    executor = ToolExecutor(resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={})

    attach_to_tree(tree, plan, executor=executor)

    command = tree.get_command("조회")
    assert isinstance(command, discord.app_commands.Command)

    params = command._params
    assert set(params) == {"song", "count"}
    assert params["song"].required is True
    assert params["song"].type is discord.AppCommandOptionType.string
    assert params["count"].required is False
    assert params["count"].type is discord.AppCommandOptionType.integer
    assert params["count"].default == 3
