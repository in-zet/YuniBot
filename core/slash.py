# core/slash.py
from __future__ import annotations

import enum
import keyword
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from core.decorators import SlashOnlySpec, ToolSpec
from core.executor import ToolExecutor
from core.schema import ArgSpec, build_arg_schema
from core.tool_types import ToolContext

if TYPE_CHECKING:
    from core.registry import LoadedSkill


class SlashPlanError(ValueError):
    pass


@dataclass(slots=True, kw_only=True)
class SlashPlanEntry:
    path: tuple[str, ...]
    skill_name: str
    skill_tags: list[str]
    skill_tags_any: list[str]
    spec: ToolSpec | SlashOnlySpec


@dataclass(slots=True, kw_only=True)
class SlashPlan:
    entries: list[SlashPlanEntry]


def build_slash_plan(specs: list[tuple[LoadedSkill, ToolSpec | SlashOnlySpec]]) -> SlashPlan:
    entries: list[SlashPlanEntry] = []
    for skill, spec in specs:
        if not spec.slash:
            continue
        path = tuple(spec.slash.split())
        if len(path) > 3:
            raise SlashPlanError(f"슬래시 경로는 최대 3단계다: '{spec.slash}'")
        entries.append(
            SlashPlanEntry(
                path=path,
                skill_name=skill.manifest.name,
                skill_tags=list(getattr(skill.manifest, "tags", [])),
                skill_tags_any=list(getattr(skill.manifest, "tags_any", [])),
                spec=spec,
            )
        )
    return SlashPlan(entries=entries)


def attach_to_tree(tree: app_commands.CommandTree, plan: SlashPlan, *, executor: ToolExecutor) -> None:
    groups: dict[tuple[str, ...], app_commands.Group] = {}

    def _get_group(path: tuple[str, ...]) -> app_commands.Group:
        if path in groups:
            return groups[path]
        group = app_commands.Group(name=path[-1], description=f"{'/'.join(path)} 묶음")
        if len(path) == 1:
            tree.add_command(group)
        else:
            parent = _get_group(path[:-1])
            parent.add_command(group)
        groups[path] = group
        return group

    for entry in plan.entries:
        command = _build_command(entry, executor=executor)
        if len(entry.path) == 1:
            tree.add_command(command)
        else:
            parent = _get_group(entry.path[:-1])
            parent.add_command(command)


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
    아니다). 그래도 생성한 소스에 그대로 꽂아 넣기 전에 식별자인지, 그리고 이 함수가 내부적으로
    예약한 이름(`interaction` 파라미터 자신을 포함해 전부 `__`로 시작한다)과 겹치지 않는지
    다시 확인한다. 도구 인자 이름이 `interaction`이어도 괜찮도록 생성한 함수의 실제 첫
    파라미터는 `__interaction`으로 둔다(다른 내부 식별자처럼 전부 `__` 접두어를 쓴다) — 그래서
    도구 인자 이름이 `__`로 시작하는 경우만 막으면 이 함수가 쓰는 어떤 내부 이름과도 겹칠 수
    없다(`__options`, `__dispatch`, `__Interaction`, `__type_*`, `__default_*`, `__interaction`).
    """
    for arg in args:
        if not arg.name.isidentifier() or keyword.iskeyword(arg.name) or arg.name.startswith("__"):
            reason = "식별자가 아니거나, 예약어이거나, '__'로 시작해 내부 이름과 겹친다"
            raise SlashPlanError(f"도구 인자 이름 '{arg.name}'은 슬래시 콜백에 쓸 수 없다({reason})")

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


def _build_command(entry: SlashPlanEntry, *, executor: ToolExecutor) -> app_commands.Command[Any, Any, Any]:
    schema = build_arg_schema(entry.spec.fn)
    fn_name = f"slash_{entry.skill_name}_{entry.spec.name}".replace("-", "_")
    if not fn_name.isidentifier():
        # entry.spec.name은 실제 함수의 __name__이라 늘 식별자이지만, skill_name은
        # manifest에서 온 문자열이라 보장이 없다. exec로 소스를 만들기 전에 반드시
        # 확인한다 — 그래야 만들어 내는 소스 문자열에 임의 코드가 끼어들 수 없다.
        raise SlashPlanError(f"스킬 이름 '{entry.skill_name}'은 슬래시 콜백 이름으로 쓸 수 없다")

    async def _dispatch(interaction: discord.Interaction, options: dict[str, Any]) -> None:
        player_id = str(interaction.user.id)
        tags = frozenset(await executor.tag_provider.tags_for(player_id))

        ctx = ToolContext(player_id=player_id, tags=tags, services={})
        outcome = await executor.invoke(
            entry.spec, ctx, options, skill_tags=entry.skill_tags, skill_tags_any=entry.skill_tags_any
        )

        if outcome.needs_confirm:
            await interaction.response.send_message(
                "확인해 주세요.", view=_ConfirmView(executor=executor, token=outcome.confirm_token or "")
            )
            return

        result = outcome.result
        assert result is not None
        if result.error:
            await interaction.response.send_message(result.error, ephemeral=True)
            return

        display = result.display
        send_kwargs: dict[str, Any] = {
            "content": display.content if display else None,
            "embeds": display.embeds if display else [],
        }
        if display and display.view is not None:
            send_kwargs["view"] = display.view
        await interaction.response.send_message(**send_kwargs)

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


class _ConfirmView(discord.ui.View):
    def __init__(self, *, executor: ToolExecutor, token: str) -> None:
        super().__init__(timeout=120)
        self._executor = executor
        self._token = token

    @discord.ui.button(label="확인", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        result = await self._executor.confirm(self._token, confirmed=True)
        await interaction.response.send_message(result.error or "완료했다.")

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        result = await self._executor.confirm(self._token, confirmed=False)
        await interaction.response.send_message(result.error or "취소했다.")
