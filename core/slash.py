# core/slash.py
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from core.decorators import SlashOnlySpec, ToolSpec
from core.executor import ToolExecutor
from core.schema import ArgSpec, build_arg_schema
from core.slash_rules import callback_arg_name_problems, slash_path_problems
from core.tool_types import ToolContext, ToolResult
from services.common.players import PlayersService

if TYPE_CHECKING:
    from core.registry import LoadedSkill

__all__ = ["callback_arg_name_problems", "slash_path_problems"]  # 테스트/호출자 편의를 위한 재노출

logger = logging.getLogger(__name__)

_DISCORD_CONTENT_LIMIT = 2000


class SlashPlanError(ValueError):
    pass


# --- 계획과 트리 ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class SlashPlanEntry:
    path: tuple[str, ...]
    skill_name: str
    skill_tags: list[str]
    skill_tags_any: list[str]
    spec: ToolSpec | SlashOnlySpec
    skill_services: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class SlashPlan:
    entries: list[SlashPlanEntry]


def build_slash_plan(specs: list[tuple[LoadedSkill, ToolSpec | SlashOnlySpec]]) -> SlashPlan:
    """경로 검사는 하지 않는다. 경로 규칙 위반은 레지스트리가 스킬 단위로 먼저 걸러내고,
    attach_to_tree가 스킬 단위로 한 번 더 확인한다 — 여기서 예외를 던지면 한 스킬의 결함이
    다른 모든 스킬의 계획까지 날려 버린다."""
    entries: list[SlashPlanEntry] = []
    for skill, spec in specs:
        if spec.slash is None:
            continue
        manifest = skill.manifest
        entries.append(
            SlashPlanEntry(
                path=tuple(spec.slash.split()),
                skill_name=manifest.name,
                skill_tags=list(getattr(manifest, "tags", [])),
                skill_tags_any=list(getattr(manifest, "tags_any", [])),
                skill_services=list(getattr(manifest, "services", [])),
                spec=spec,
            )
        )
    return SlashPlan(entries=entries)


_Container = app_commands.Group | None  # None은 트리 최상위


def attach_to_tree(tree: app_commands.CommandTree[Any], plan: SlashPlan, *, executor: ToolExecutor) -> None:
    """스킬 단위로 명령을 붙인다. 한 스킬의 명령을 만들거나 붙이다가 무엇이든 실패하면, 그 스킬이
    이미 붙인 명령/그룹을 되돌리고 경고를 남긴 뒤 다음 스킬로 넘어간다(다른 스킬은 영향 없음).
    레지스트리 검증이 1차 방어선이고, 이것은 그 검증을 빠져나온 결함에 대한 2차 방어선이다."""
    groups: dict[tuple[str, ...], app_commands.Group] = {}

    by_skill: dict[str, list[SlashPlanEntry]] = {}
    for entry in plan.entries:
        by_skill.setdefault(entry.skill_name, []).append(entry)

    for skill_name, entries in by_skill.items():
        try:
            _attach_skill(tree, groups, entries, executor=executor)
        except Exception:
            logger.warning("스킬 '%s'의 슬래시 명령을 등록하지 못해 건너뛴다", skill_name, exc_info=True)


def _attach_skill(
    tree: app_commands.CommandTree[Any],
    groups: dict[tuple[str, ...], app_commands.Group],
    entries: list[SlashPlanEntry],
    *,
    executor: ToolExecutor,
) -> None:
    # 1) 트리를 건드리기 전에 이 스킬의 명령을 전부 만든다(검사 실패/생성 실패는 여기서 끝난다).
    built: list[tuple[tuple[str, ...], app_commands.Command[Any, Any, Any]]] = []
    for entry in entries:
        problems = slash_path_problems(" ".join(entry.path))
        if problems:
            raise SlashPlanError("; ".join(problems))
        built.append((entry.path, _build_command(entry, executor=executor)))

    # 2) 붙인다. 중간에 실패하면 이 스킬이 붙인 것만 되돌린다.
    added: list[tuple[_Container, app_commands.Command[Any, Any, Any] | app_commands.Group]] = []
    created_groups: list[tuple[str, ...]] = []

    def _add(parent: _Container, item: app_commands.Command[Any, Any, Any] | app_commands.Group) -> None:
        try:
            if parent is None:
                tree.add_command(item)
            else:
                parent.add_command(item)
        except Exception:
            # Group.add_command는 25개 초과일 때 넣은 뒤에 ValueError를 던진다 — 우리 것이면 뺀다.
            _remove_if_ours(tree, parent, item)
            raise
        added.append((parent, item))

    def _ensure_group(prefix: tuple[str, ...]) -> _Container:
        if not prefix:
            return None
        if prefix in groups:
            return groups[prefix]
        parent = _ensure_group(prefix[:-1])
        group = app_commands.Group(name=prefix[-1], description=f"{'/'.join(prefix)} 묶음")
        _add(parent, group)
        groups[prefix] = group
        created_groups.append(prefix)
        return group

    try:
        for path, command in built:
            _add(_ensure_group(path[:-1]), command)
    except Exception:
        for parent, item in reversed(added):
            _remove_if_ours(tree, parent, item)
        for prefix in created_groups:
            _ = groups.pop(prefix, None)
        raise


def _remove_if_ours(
    tree: app_commands.CommandTree[Any],
    parent: _Container,
    item: app_commands.Command[Any, Any, Any] | app_commands.Group,
) -> None:
    current = tree.get_command(item.name) if parent is None else parent.get_command(item.name)
    if current is not item:
        return  # 다른 스킬의 명령이다. 건드리지 않는다.
    if parent is None:
        _ = tree.remove_command(item.name)
    else:
        _ = parent.remove_command(item.name)


def _is_enum(py_type: Any) -> bool:
    return isinstance(py_type, type) and issubclass(py_type, enum.Enum)


def _display_type(arg: ArgSpec) -> Any:
    """discord.py에 보여줄 옵션 타입. 표준 인자 타입과 enum은 원문 그대로(str)로 받아
    ToolExecutor.invoke의 해석기/도구 코드가 실제 값으로 바꾼다(core/schema.py, core/executor.py 참고)."""
    return str if (arg.is_ref_type or _is_enum(arg.py_type)) else arg.py_type


def _build_callback_source(fn_name: str, args: list[ArgSpec]) -> str:
    """discord.py의 `app_commands.Command`는 콜백의 실제 `inspect.Parameter` 목록에서
    옵션을 뽑아낸다(`_extract_parameters_from_callback`은 `inspect.signature(func).parameters`를
    그대로 순회하고, `POSITIONAL_ONLY`/`VAR_POSITIONAL`/`VAR_KEYWORD` kind는 명시적으로
    거부한다). 그래서 `async def callback(interaction, **options: Any)`처럼 만든 뒤
    `__annotations__`만 덮어쓰는 방식은 통하지 않는다 — 실제로 만들어 보면 VAR_KEYWORD
    파라미터라는 이유로 `app_commands.Command(...)` 생성 시점에 `TypeError`가 난다(annotation을
    비워 둬도, `Any`로 채워도 마찬가지다). 그래서 도구 인자 하나당 진짜 이름 붙은 파라미터가
    하나씩 있는 `async def` 소스를 직접 만들어 `exec`한다.

    인자 이름은 전부 신뢰하는 스킬 코드의 실제 함수 파라미터 이름에서 온다(사용자 입력이
    아니다). 레지스트리가 로드 시점에 callback_arg_name_problems로 이미 걸러내지만, 생성한
    소스에 그대로 꽂아 넣기 전에 여기서도 다시 확인한다. 도구 인자 이름이 `interaction`이어도
    괜찮도록 생성한 함수의 실제 첫 파라미터는 `__interaction`으로 둔다(다른 내부 식별자처럼
    전부 `__` 접두어를 쓴다) — 그래서 도구 인자 이름이 `__`로 시작하는 경우만 막으면 이 함수가
    쓰는 어떤 내부 이름과도 겹칠 수 없다(`__options`, `__dispatch`, `__Interaction`, `__type_*`,
    `__default_*`, `__interaction`).
    """
    problems = callback_arg_name_problems(arg.name for arg in args)
    if problems:
        raise SlashPlanError("; ".join(problems))

    params = ["__interaction: __Interaction"]
    if args:
        params.append("*")
    for arg in args:
        type_ref = f"__type_{arg.name}"
        if arg.required:
            params.append(f"{arg.name}: {type_ref}")
        else:
            params.append(f"{arg.name}: {type_ref} = __default_{arg.name}")

    assign_lines = "\n".join(f"    __options[{arg.name!r}] = {arg.name}" for arg in args)
    body = f"    __options = {{}}\n{assign_lines}\n" if args else "    __options = {}\n"
    source = (
        f"async def {fn_name}({', '.join(params)}) -> None:\n"
        f"{body}"
        f"    await __dispatch(__interaction, __options)\n"
    )
    return source


# --- 결과 보내기 ---------------------------------------------------------------------------


def _fallback_content(data: dict[str, Any]) -> str:
    # 표시물이 없는 결과도 디스코드에 빈 응답을 보내면 안 된다("This interaction failed").
    # "_"로 시작하는 키는 코어 내부 표시라 보여주지 않는다.
    lines = [f"{key}: {value}" for key, value in data.items() if not key.startswith("_")]
    return "\n".join(["완료했다.", *lines])


def render_result(result: ToolResult) -> dict[str, Any]:
    """ToolResult를 `interaction.response.send_message(**kwargs)`에 넘길 인자로 바꾼다.

    슬래시 직접 실행과 확인/취소 버튼이 모두 이 함수 하나를 쓴다. 돌려주는 인자는 언제나
    비어 있지 않은 content, embeds, view 중 하나 이상을 담는다."""
    if result.error:
        return {"content": result.error[:_DISCORD_CONTENT_LIMIT], "ephemeral": True}

    display = result.display
    if display is not None and (display.content or display.embeds or display.view is not None):
        kwargs: dict[str, Any] = {"content": display.content, "embeds": list(display.embeds)}
        if display.view is not None:
            kwargs["view"] = display.view
        return kwargs

    return {"content": _fallback_content(result.data)[:_DISCORD_CONTENT_LIMIT]}


async def _send_result(interaction: discord.Interaction, result: ToolResult) -> None:
    await interaction.response.send_message(**render_result(result))


def _build_command(entry: SlashPlanEntry, *, executor: ToolExecutor) -> app_commands.Command[Any, Any, Any]:
    schema = build_arg_schema(entry.spec.fn)
    fn_name = f"slash_{entry.skill_name}_{entry.spec.name}".replace("-", "_")
    if not fn_name.isidentifier():
        # 레지스트리(manifest.name 검사)가 먼저 막지만, exec로 소스를 만들기 전에 반드시 다시
        # 확인한다 — 그래야 만들어 내는 소스 문자열에 임의 코드가 끼어들 수 없다.
        raise SlashPlanError(f"스킬 이름 '{entry.skill_name}'은 슬래시 콜백 이름으로 쓸 수 없다")

    async def _dispatch(interaction: discord.Interaction, options: dict[str, Any]) -> None:
        discord_id = str(interaction.user.id)
        players: PlayersService = executor.services["players"]  # pyright: ignore[reportAssignmentType]

        player = await players.player_for_discord(discord_id)
        is_new_player = player is None
        if player is None:
            player = await players.create_player()
            await players.link_account(player.id, discord_id)

        tags = frozenset(await executor.tag_provider.tags_for(player.id))
        skill_services = {
            name: executor.services[name] for name in entry.skill_services if name in executor.services
        }
        ctx = ToolContext(
            player_id=player.id,
            tags=tags,
            services=skill_services,
            guild_id=str(interaction.guild_id) if interaction.guild_id else None,
            event_bus=executor.event_bus,
            player_data=executor.player_data,
        )

        if is_new_player:
            await executor.event_bus.emit_notify("players.first_seen", {"player_id": player.id})

        outcome = await executor.invoke(
            entry.spec,
            ctx,
            options,
            skill_tags=entry.skill_tags,
            skill_tags_any=entry.skill_tags_any,
            skill_name=entry.skill_name,
        )

        if outcome.needs_confirm:
            view = ConfirmView(executor=executor, token=outcome.confirm_token or "", requester_id=player.id)
            await interaction.response.send_message("확인해 주세요.", view=view)
            return

        result = outcome.result
        assert result is not None
        await _send_result(interaction, result)

    source = _build_callback_source(fn_name, schema.args)
    namespace: dict[str, Any] = {"__Interaction": discord.Interaction, "__dispatch": _dispatch}
    for arg in schema.args:
        namespace[f"__type_{arg.name}"] = _display_type(arg)
        if not arg.required:
            namespace[f"__default_{arg.name}"] = arg.default

    code = compile(source, f"<slash:{entry.skill_name}.{entry.spec.name}>", "exec")
    exec(code, namespace)  # noqa: S102 - 신뢰하는 스킬 코드의 인자 이름/타입만으로 생성한 소스
    callback = namespace[fn_name]
    callback.__doc__ = entry.spec.fn.__doc__

    return app_commands.Command(
        name=entry.path[-1],
        description=(entry.spec.fn.__doc__ or "").strip().splitlines()[0][:100],
        callback=callback,
    )


class ConfirmView(discord.ui.View):
    """confirm="always" 도구의 확인/취소 버튼. 요청한 플레이어 본인만 누를 수 있다.

    본인 확인은 두 겹이다: 여기 interaction_check가 디스코드 UI 단계에서 먼저 막고,
    ToolExecutor.confirm(player_id=...)이 실행기 단계에서 다시 막는다."""

    def __init__(
        self,
        *,
        executor: ToolExecutor,
        token: str,
        requester_id: str,
    ) -> None:
        super().__init__(timeout=120)
        self._executor = executor
        self._token = token
        self._requester_id = requester_id

    async def _player_id_for(self, interaction: discord.Interaction) -> str:
        players: PlayersService = self._executor.services["players"]  # pyright: ignore[reportAssignmentType]
        player = await players.player_for_discord(str(interaction.user.id))
        return player.id if player is not None else str(interaction.user.id)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if await self._player_id_for(interaction) == self._requester_id:
            return True
        await interaction.response.send_message("이건 당신의 확인 요청이 아니에요.", ephemeral=True)
        return False

    async def _finish(self, interaction: discord.Interaction, *, confirmed: bool) -> None:
        player_id = await self._player_id_for(interaction)
        result = await self._executor.confirm(self._token, confirmed=confirmed, player_id=player_id)
        await _send_result(interaction, result)

    @discord.ui.button(label="확인", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        await self._finish(interaction, confirmed=True)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        await self._finish(interaction, confirmed=False)
