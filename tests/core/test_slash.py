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


@tool(slash="충돌")
async def arg_named_interaction(ctx: ToolContext, interaction: str) -> ToolResult:
    """도구 인자 이름이 생성한 콜백의 내부 예약 이름(`interaction`)과 고의로 겹친다."""
    return ToolResult(data={"interaction": interaction})


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

    names = {p.name for p in command.parameters}
    assert names == {"song", "count"}

    song = command.get_parameter("song")
    count = command.get_parameter("count")
    assert song is not None
    assert count is not None
    assert song.required is True
    assert song.type is discord.AppCommandOptionType.string
    assert count.required is False
    assert count.type is discord.AppCommandOptionType.integer
    assert count.default == 3


def test_attach_to_tree_handles_tool_arg_named_interaction_without_crashing() -> None:
    """생성한 콜백의 "진짜" interaction 파라미터가 그냥 `interaction`이라는 이름이면, 도구 인자
    이름도 우연히 `interaction`일 때 `compile()`이
    `SyntaxError: duplicate argument 'interaction' in function definition`을 던진다 — 봇 시작을
    낮은 수준의 알아보기 힘든 에러로 깨뜨린다. 고친 방식은 생성한 콜백의 interaction 파라미터
    이름 자체를 다른 내부 이름들처럼 `__interaction`으로 예약해 두어서, 도구 인자 이름이
    `interaction`이어도 전혀 겹치지 않는다 — 그래서 이 입력은 더 이상 막을 필요가 없고 그냥
    정상적으로 동작해야 한다(SyntaxError도, SlashPlanError도 나지 않는다)."""
    from core.executor import ToolExecutor
    from core.permissions import InMemoryTagProvider
    from core.resolvers import ResolverRegistry
    from core.slash import attach_to_tree

    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    plan = build_slash_plan(
        [(_FakeSkill("x"), arg_named_interaction.__yuni_tool__)]  # pyright: ignore[reportFunctionMemberAccess, reportArgumentType]
    )
    executor = ToolExecutor(resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={})

    attach_to_tree(tree, plan, executor=executor)

    command = tree.get_command("충돌")
    assert isinstance(command, discord.app_commands.Command)
    param = command.get_parameter("interaction")
    assert param is not None
    assert param.required is True
    assert param.type is discord.AppCommandOptionType.string


def test_build_callback_source_rejects_dunder_prefixed_arg_names() -> None:
    """`__options`처럼 이 모듈이 내부적으로 쓰는 이름과 똑같은 도구 인자는 생성한 함수 본문에서
    자기 자신을 가리키게 되어(예: `__options = {}` 뒤 `__options['__options'] = __options`) 전달된
    실제 값이 조용히 사라진다(크래시 없이 틀린 값). `_build_callback_source`가 `__`로 시작하는
    인자 이름을 전부 막아서 이 경우를 포함한 어떤 내부 이름과의 충돌도 미리 거른다."""
    from core.schema import ArgSpec
    from core.slash import _build_callback_source  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(SlashPlanError, match="__options"):
        _build_callback_source(
            "fn", [ArgSpec(name="__options", py_type=str, required=True)]
        )
