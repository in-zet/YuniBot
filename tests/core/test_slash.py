import logging
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import aiosqlite
import discord
import pytest

from core.decorators import tool
from core.executor import ToolExecutor
from core.permissions import InMemoryTagProvider
from core.registry import SkillRegistry
from core.registry_snapshot import RegistrySnapshot
from core.resolvers import ResolverRegistry
from core.slash import (
    ConfirmView,
    SlashPlanError,
    attach_to_tree,
    build_slash_plan,
    render_result,
    slash_path_problems,
)
from core.tool_types import Display, ToolContext, ToolResult
from services.common.players import PlayersService

FIXTURES = Path(__file__).parent.parent / "fixtures"

_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)

# 이 모듈의 많은 테스트는 attach_to_tree의 새 db 매개변수를 쓰지 않는다(ctx.db에 접근하는
# 도구가 없다) — 매번 실제 연결을 여는 대신 타입만 맞춘 가짜를 하나 공유한다.
_NO_DB = cast(aiosqlite.Connection, object())
# 마찬가지로, 이 모듈의 테스트 대부분은 ctx.registry_snapshot을 쓰는 도구도 다루지 않는다.
_NO_SNAPSHOT = RegistrySnapshot(skills=(), failures={})


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


def test_attach_to_tree_skips_more_than_three_levels_without_crashing() -> None:
    # build_slash_plan은 더 이상 던지지 않는다(한 스킬 때문에 모든 스킬의 계획이 날아가면 안 된다).
    # 깊이 규칙은 레지스트리가 1차로, attach_to_tree가 스킬 단위로 2차로 막는다.
    @tool(slash="너무 깊은 명령 경로 넷")
    async def too_deep(ctx: ToolContext) -> ToolResult:
        """4단계 깊이."""
        return ToolResult(data={})

    plan = build_slash_plan(
        [  # pyright: ignore[reportArgumentType]
            (_FakeSkill("x"), too_deep.__yuni_tool__),  # pyright: ignore[reportFunctionMemberAccess]
            (_FakeSkill("border"), standalone.__yuni_tool__),  # pyright: ignore[reportFunctionMemberAccess]
        ]
    )
    executor = ToolExecutor(resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={})
    tree: discord.app_commands.CommandTree[Any] = discord.app_commands.CommandTree(
        discord.Client(intents=discord.Intents.none())
    )

    attach_to_tree(tree, plan, executor=executor, db=_NO_DB, registry_snapshot=_NO_SNAPSHOT)

    assert tree.get_command("너무") is None
    assert tree.get_command("보더") is not None


def test_slash_path_problems_reports_depth() -> None:
    assert any("3단계" in p for p in slash_path_problems("a b c d"))
    assert slash_path_problems("연결 시작") == []


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

    attach_to_tree(tree, plan, executor=executor, db=_NO_DB, registry_snapshot=_NO_SNAPSHOT)

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

    attach_to_tree(tree, plan, executor=executor, db=_NO_DB, registry_snapshot=_NO_SNAPSHOT)

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

    attach_to_tree(tree, plan, executor=executor, db=_NO_DB, registry_snapshot=_NO_SNAPSHOT)

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


# --- 결과 렌더링, 데모 태그 부여, 확인 버튼 ------------------------------------------------


class _FakeResponse:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, content: str | None = None, **kwargs: Any) -> None:
        self.calls.append({"content": content, **kwargs})


class _FakeInteraction:
    """슬래시 콜백과 버튼 콜백이 실제로 쓰는 속성(user.id, guild_id, response.send_message)만 흉내 낸다."""

    def __init__(self, user_id: int, *, guild_id: int | None = None) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.guild_id = guild_id
        self.response = _FakeResponse()


def _fake(user_id: int) -> Any:
    # discord.Interaction 자리에 넘기므로 타입 검사기에는 Any로 보인다.
    return _FakeInteraction(user_id)


@pytest.fixture
async def practice_tree() -> AsyncIterator[tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService]]:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_PLAYERS_MIGRATION)
    await conn.commit()
    players = PlayersService(conn)

    registry = SkillRegistry([FIXTURES / "skills_ok"])
    registry.load()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(),
        tag_provider=players,
        tag_definitions=registry.tag_definitions(),
        services={"players": players},
    )
    client = discord.Client(intents=discord.Intents.none())
    tree: discord.app_commands.CommandTree[Any] = discord.app_commands.CommandTree(client)
    attach_to_tree(
        tree, build_slash_plan(registry.tool_specs()), executor=executor, db=conn, registry_snapshot=_NO_SNAPSHOT
    )
    try:
        yield tree, executor, players
    finally:
        await conn.close()


def _practice_command(tree: discord.app_commands.CommandTree[Any], sub: str) -> discord.app_commands.Command[Any, Any, Any]:
    group = tree.get_command("연습")
    assert isinstance(group, discord.app_commands.Group)
    command = group.get_command(sub)
    assert isinstance(command, discord.app_commands.Command)
    return command


def test_render_result_without_display_is_never_empty() -> None:
    # 디스코드는 content도, embeds도, view도 없는 응답을 거부한다("This interaction failed").
    kwargs = render_result(ToolResult(data={"foo": "bar"}))

    assert kwargs["content"]
    assert "foo: bar" in kwargs["content"]


def test_render_result_without_display_or_data_is_never_empty() -> None:
    assert render_result(ToolResult())["content"]


def test_render_result_passes_display_through() -> None:
    display = Display(content="표시물", embeds=[])
    kwargs = render_result(ToolResult(data={"foo": "bar"}, display=display))

    assert kwargs["content"] == "표시물"
    assert "view" not in kwargs


def test_render_result_error_is_ephemeral() -> None:
    kwargs = render_result(ToolResult(error="안 돼요"))

    assert kwargs == {"content": "안 돼요", "ephemeral": True}


def test_render_result_display_not_ephemeral_by_default() -> None:
    display = Display(content="표시물")
    kwargs = render_result(ToolResult(data={}, display=display))

    assert "ephemeral" not in kwargs


def test_render_result_passes_display_ephemeral_through() -> None:
    display = Display(content="너만 보여요", ephemeral=True)
    kwargs = render_result(ToolResult(data={}, display=display))

    assert kwargs["ephemeral"] is True


async def test_data_only_tool_reply_has_content(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
) -> None:
    tree, _, players = practice_tree
    player = await players.create_player()
    await players.link_account(player.id, "1")
    await players.grant_tag(player.id, "practice-unlocked", source="test")
    interaction = _fake(1)

    await _practice_command(tree, "보기").callback(interaction)  # pyright: ignore[reportCallIssue]

    [call] = interaction.response.calls
    assert call["content"]
    assert not call.get("ephemeral")


async def test_start_demo_grants_practice_unlocked_tag_and_unlocks_view(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
) -> None:
    tree, _, players = practice_tree
    view_command = _practice_command(tree, "보기")

    locked = _fake(42)
    await view_command.callback(locked)  # pyright: ignore[reportCallIssue]
    assert locked.response.calls[0].get("ephemeral") is True  # 아직 잠겨 있다

    start = _fake(42)
    await _practice_command(tree, "체험시작").callback(start)  # pyright: ignore[reportCallIssue]
    assert start.response.calls[0]["content"]

    player = await players.player_for_discord("42")
    assert player is not None
    assert "practice-unlocked" in await players.tags_for(player.id)

    unlocked = _fake(42)
    await view_command.callback(unlocked)  # pyright: ignore[reportCallIssue]
    assert not unlocked.response.calls[0].get("ephemeral")

    # 다른 디스코드 계정은 참조도 안 됐으니 플레이어 자체가 아직 없다.
    assert await players.player_for_discord("7") is None


async def _issue_reset_confirm(
    tree: discord.app_commands.CommandTree[Any], user_id: int
) -> ConfirmView:
    interaction = _fake(user_id)
    await _practice_command(tree, "초기화").callback(interaction)  # pyright: ignore[reportCallIssue]
    [call] = interaction.response.calls
    view = call["view"]
    assert isinstance(view, ConfirmView)
    return view


async def test_confirm_view_rejects_other_players(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
) -> None:
    tree, _, _ = practice_tree
    view = await _issue_reset_confirm(tree, user_id=1)

    intruder = _fake(2)
    allowed = await view.interaction_check(intruder)  # pyright: ignore[reportArgumentType]

    assert allowed is False
    [call] = intruder.response.calls
    assert call.get("ephemeral") is True

    owner = _fake(1)
    assert await view.interaction_check(owner) is True  # pyright: ignore[reportArgumentType]
    assert owner.response.calls == []


async def test_confirm_button_shows_tool_output(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
) -> None:
    tree, _, _ = practice_tree
    view = await _issue_reset_confirm(tree, user_id=1)

    owner = _fake(1)
    await view.confirm_button.callback(owner)  # pyright: ignore[reportCallIssue, reportUnknownMemberType]

    [call] = owner.response.calls
    assert "reset: True" in call["content"]


async def test_confirm_button_handler_passes_presser_identity_to_executor(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
) -> None:
    # interaction_check를 건너뛰어도(두 번째 방어선) 실행기가 다른 플레이어의 확인을 거부해야 한다.
    tree, _, _ = practice_tree
    view = await _issue_reset_confirm(tree, user_id=1)

    intruder = _fake(2)
    await view.confirm_button.callback(intruder)  # pyright: ignore[reportCallIssue, reportUnknownMemberType]
    [call] = intruder.response.calls
    assert call.get("ephemeral") is True
    assert "reset" not in (call["content"] or "")

    owner = _fake(1)
    await view.confirm_button.callback(owner)  # pyright: ignore[reportCallIssue, reportUnknownMemberType]
    assert "reset: True" in owner.response.calls[0]["content"]


def test_attach_to_tree_skips_a_broken_skill_and_keeps_the_rest(caplog: pytest.LogCaptureFixture) -> None:
    # 레지스트리 검증을 빠져나온 결함이 있어도(방어선 2) 한 스킬의 명령 생성 실패가 나머지 스킬의
    # 명령 등록을 막지 않는다. 여기서는 레지스트리를 거치지 않고 일부러 같은 경로를 두 스킬에 준다.
    executor = ToolExecutor(resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={})
    client = discord.Client(intents=discord.Intents.none())
    tree: discord.app_commands.CommandTree[Any] = discord.app_commands.CommandTree(client)

    @tool(slash="연결 시작 하나")
    async def nested(ctx: ToolContext) -> ToolResult:
        """연결 시작을 그룹으로 쓴다."""
        return ToolResult(data={})

    plan = build_slash_plan(
        [  # pyright: ignore[reportArgumentType]
            (_FakeSkill("good"), sub1.__yuni_tool__),  # pyright: ignore[reportFunctionMemberAccess]
            (_FakeSkill("broken"), standalone.__yuni_tool__),  # pyright: ignore[reportFunctionMemberAccess]
            (_FakeSkill("broken"), nested.__yuni_tool__),  # pyright: ignore[reportFunctionMemberAccess]
            (_FakeSkill("other"), lookup.__yuni_tool__),  # pyright: ignore[reportFunctionMemberAccess]
        ]
    )

    with caplog.at_level(logging.WARNING):
        attach_to_tree(tree, plan, executor=executor, db=_NO_DB, registry_snapshot=_NO_SNAPSHOT)

    group = tree.get_command("연결")
    assert isinstance(group, discord.app_commands.Group)
    start = group.get_command("시작")
    assert isinstance(start, discord.app_commands.Command)  # good의 명령이 그대로 남는다
    assert tree.get_command("조회") is not None
    assert tree.get_command("보더") is None  # broken 스킬의 명령은 하나도 남지 않는다(부분 등록 없음)
    assert "broken" in caplog.text
