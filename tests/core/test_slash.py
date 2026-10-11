import logging
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import aiosqlite
import discord
import pytest

from core.decorators import slash_only, tool
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


class _FakeModal(discord.ui.Modal, title="테스트 모달"):
    pass


@slash_only("모달테스트")
async def open_test_modal(ctx: ToolContext) -> discord.ui.Modal:
    """테스트용 입력창을 연다."""
    return _FakeModal()


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
    """core.slash.respond()가 defer() 뒤 쓰는 followup.send/edit_original_response까지
    흉내 낸다 — 실제 디스코드처럼 defer 이후엔 send_message를 다시 못 쓴다. 어느 경로로
    가든 같은 `_FakeInteraction.calls`에 쌓여 기존 단언이 그대로 통한다."""

    def __init__(self, owner: _FakeInteraction) -> None:
        self._owner = owner
        self._deferred = False

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._owner.calls

    async def send_message(self, content: str | None = None, **kwargs: Any) -> None:
        self._owner.calls.append({"content": content, **kwargs})

    async def send_modal(self, modal: Any) -> None:
        self._owner.calls.append({"modal": modal})

    async def defer(self, *, ephemeral: bool = False) -> None:
        self._deferred = True

    def is_done(self) -> bool:
        return self._deferred


class _FakeFollowup:
    def __init__(self, owner: _FakeInteraction) -> None:
        self._owner = owner

    async def send(self, content: str | None = None, **kwargs: Any) -> None:
        self._owner.calls.append({"content": content, **kwargs})


class _FakeInteraction:
    """슬래시 콜백과 버튼 콜백이 실제로 쓰는 속성(user.id, guild_id, response.send_message)만 흉내 낸다."""

    def __init__(self, user_id: int, *, guild_id: int | None = None) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.guild_id = guild_id
        self.calls: list[dict[str, Any]] = []
        self.response = _FakeResponse(self)
        self.followup = _FakeFollowup(self)

    async def edit_original_response(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    async def delete_original_response(self) -> None:
        pass


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


async def test_dispatch_defers_the_interaction_before_responding(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
) -> None:
    """디스코드는 최초 응답을 3초 안에 받아야 한다. 도구 본문(특히 notify.dm처럼 디스코드 API를
    한 번 더 왕복하는 호출)이 그 기한을 넘기면, defer 없이는 "Unknown interaction"(10062)으로
    조용히 실패한다(실제로 재현됐다) — 그래서 _dispatch는 도구를 부르기 전에 반드시 먼저
    defer부터 한다. 이 테스트는 defer가 실제로 불렸는지, 그리고 defer 뒤에도 최종 응답이
    (followup/edit_original_response 경로로) 정확히 한 번 도착하는지를 함께 확인한다."""
    tree, _, players = practice_tree
    player = await players.create_player()
    await players.link_account(player.id, "1")
    await players.grant_tag(player.id, "practice-unlocked", source="test")
    interaction = _fake(1)

    await _practice_command(tree, "보기").callback(interaction)  # pyright: ignore[reportCallIssue]

    assert interaction.response.is_done()
    [call] = interaction.response.calls
    assert call["content"]


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


# --- manifest.services로 막는 코어 전용 맥락(db, player_data, registry_snapshot) -------------


class _FakeSkillWithServices:
    def __init__(self, name: str, services: list[str]) -> None:
        self.manifest = type("M", (), {"name": name, "tags": [], "tags_any": [], "services": services})()


async def test_dispatch_gives_db_player_data_snapshot_only_to_skills_that_declare_them() -> None:
    """리뷰(최종) Fix 3: _dispatch가 db/player_data/registry_snapshot을 모든 스킬에게 무조건 주면
    services: [players]만 선언한 스킬도 임의 테이블을 읽고 쓰거나 남의 데이터를 지울 수 있다.
    manifest.services에 적은 스킬만 받고, 나머지는 None이어야 한다."""
    from core.player_data import PlayerDataScanner

    seen: dict[str, ToolContext] = {}

    @tool(slash="게이트없음")
    async def undeclared_tool(ctx: ToolContext) -> ToolResult:
        """선언 없음."""
        seen["undeclared"] = ctx
        return ToolResult(data={})

    @tool(slash="게이트있음")
    async def declared_tool(ctx: ToolContext) -> ToolResult:
        """선언 있음."""
        seen["declared"] = ctx
        return ToolResult(data={})

    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_PLAYERS_MIGRATION)
    await conn.commit()
    players = PlayersService(conn)
    player_data = PlayerDataScanner(conn=conn, table_names=())
    snapshot = RegistrySnapshot(skills=(), failures={"x": "y"})
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=players, tag_definitions={},
        services={"players": players}, player_data=player_data,
    )
    plan = build_slash_plan(
        [  # pyright: ignore[reportArgumentType]
            (_FakeSkillWithServices("plain", ["players"]), undeclared_tool.__yuni_tool__),  # pyright: ignore[reportFunctionMemberAccess]
            (
                _FakeSkillWithServices("core-ish", ["players", "db", "player_data", "registry_snapshot"]),
                declared_tool.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
            ),
        ]
    )
    tree: discord.app_commands.CommandTree[Any] = discord.app_commands.CommandTree(
        discord.Client(intents=discord.Intents.none())
    )
    attach_to_tree(tree, plan, executor=executor, db=conn, registry_snapshot=snapshot)

    try:
        for name in ("게이트없음", "게이트있음"):
            command = tree.get_command(name)
            assert isinstance(command, discord.app_commands.Command)
            await command.callback(_fake(1))  # pyright: ignore[reportCallIssue]

        plain = seen["undeclared"]
        assert plain.db is None
        assert plain.player_data is None
        assert plain.registry_snapshot is None

        declared = seen["declared"]
        assert declared.db is conn
        assert declared.player_data is player_data
        assert declared.registry_snapshot is snapshot
    finally:
        await conn.close()


async def test_practice_fixture_with_only_players_gets_no_db_or_player_data(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실제 픽스처 스킬(practice, services: [players])도 같은 규칙을 따른다."""
    tree, executor, players = practice_tree
    captured: list[ToolContext] = []
    original_invoke = executor.invoke

    async def spy(spec: Any, ctx: ToolContext, *args: Any, **kwargs: Any) -> Any:
        captured.append(ctx)
        return await original_invoke(spec, ctx, *args, **kwargs)

    monkeypatch.setattr(executor, "invoke", spy)
    player = await players.create_player()
    await players.link_account(player.id, "1")
    await players.grant_tag(player.id, "practice-unlocked", source="test")

    await _practice_command(tree, "보기").callback(_fake(1))  # pyright: ignore[reportCallIssue]

    [ctx] = captured
    assert ctx.db is None
    assert ctx.player_data is None
    assert ctx.registry_snapshot is None


async def test_real_admin_skill_receives_registry_snapshot_through_dispatch() -> None:
    """admin은 manifest에 registry_snapshot을 선언했으니 /관리 스킬이 실제 스냅샷을 받는다."""
    from core.registry_snapshot import build_snapshot
    from services.common.settings import SettingsService

    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_PLAYERS_MIGRATION + Path("migrations/core/003_settings.sql").read_text())
    await conn.commit()
    players = PlayersService(conn)
    registry = SkillRegistry([Path("skills/admin")])
    registry.load()
    assert registry.failures() == {}
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=players, tag_definitions=registry.tag_definitions(),
        services={"players": players, "settings": SettingsService(conn)},
    )
    tree: discord.app_commands.CommandTree[Any] = discord.app_commands.CommandTree(
        discord.Client(intents=discord.Intents.none())
    )
    attach_to_tree(
        tree, build_slash_plan(registry.tool_specs()), executor=executor, db=conn,
        registry_snapshot=build_snapshot(registry),
    )
    player = await players.create_player()
    await players.link_account(player.id, "1")
    await players.grant_tag(player.id, "admin", source="test")

    group = tree.get_command("관리")
    assert isinstance(group, discord.app_commands.Group)
    command = group.get_command("스킬")
    assert isinstance(command, discord.app_commands.Command)
    interaction = _fake(1)
    try:
        await command.callback(interaction)  # pyright: ignore[reportCallIssue]
    finally:
        await conn.close()

    [call] = interaction.response.calls
    assert call.get("ephemeral") is True  # 관리 계열은 전부 본인에게만 보인다
    assert "admin" in call["content"]


async def test_modal_returning_slash_only_opens_modal_without_deferring() -> None:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_PLAYERS_MIGRATION)
    await conn.commit()
    players = PlayersService(conn)
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=players, tag_definitions={}, services={"players": players},
    )
    plan = build_slash_plan(
        [(_FakeSkill("x"), open_test_modal.__yuni_slash_only__)]  # pyright: ignore[reportFunctionMemberAccess, reportArgumentType]
    )
    tree: discord.app_commands.CommandTree[Any] = discord.app_commands.CommandTree(
        discord.Client(intents=discord.Intents.none())
    )
    attach_to_tree(tree, plan, executor=executor, db=_NO_DB, registry_snapshot=_NO_SNAPSHOT)

    try:
        command = tree.get_command("모달테스트")
        assert isinstance(command, discord.app_commands.Command)
        interaction = _fake(1)
        await command.callback(interaction)  # pyright: ignore[reportCallIssue]

        [call] = interaction.calls
        assert isinstance(call.get("modal"), _FakeModal)
        assert interaction.response.is_done() is False  # defer를 부르지 않았다
    finally:
        await conn.close()  # 실패해도 닫는다 — 안 닫으면 aiosqlite 스레드 때문에 pytest가 끝나지 않는다


async def test_modal_command_checks_the_tool_tags_and_hides_the_modal_on_denial() -> None:
    @slash_only("잠긴모달", tags=["secret-tag"])
    async def locked_modal(ctx: ToolContext) -> discord.ui.Modal:
        """도구 태그가 있어야 열리는 입력창."""
        return _FakeModal()

    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_PLAYERS_MIGRATION)
    await conn.commit()
    players = PlayersService(conn)
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=players, tag_definitions={}, services={"players": players},
    )
    plan = build_slash_plan(
        [(_FakeSkill("x"), locked_modal.__yuni_slash_only__)]  # pyright: ignore[reportFunctionMemberAccess, reportArgumentType]
    )
    tree: discord.app_commands.CommandTree[Any] = discord.app_commands.CommandTree(
        discord.Client(intents=discord.Intents.none())
    )
    attach_to_tree(tree, plan, executor=executor, db=_NO_DB, registry_snapshot=_NO_SNAPSHOT)

    try:
        command = tree.get_command("잠긴모달")
        assert isinstance(command, discord.app_commands.Command)
        interaction = _fake(1)
        await command.callback(interaction)  # pyright: ignore[reportCallIssue]

        [call] = interaction.calls
        assert "modal" not in call
        assert call["content"] == "모르는 명령이에요"
    finally:
        await conn.close()
