# skills/account-link/skill.py
from __future__ import annotations

import importlib.util
import secrets
import string
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import discord

from core.decorators import slash_only
from core.slash import render_result
from core.tool_types import Display, ToolContext, ToolResult

# 스킬 폴더 이름("account-link")에 하이픈이 있어 보통의 패키지 import 경로로 쓸 수 없다
# (skills/_template/tests/test_template_example.py가 skill.py를 불러오는 것과 같은 이유).
# 그래서 같은 폴더의 models.py를 파일 경로로 직접 불러온다.
_MODELS_PATH = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("_account_link_models", _MODELS_PATH)
assert _spec is not None and _spec.loader is not None
models = importlib.util.module_from_spec(_spec)
# models.py는 `from __future__ import annotations`로 문자열 어노테이션을 쓰는
# @dataclass(kw_only=True)를 정의한다. dataclasses는 KW_ONLY 센티널 등을 확인하려고
# `sys.modules[cls.__module__]`을 되찾아 그 모듈의 전역에서 어노테이션을 풀어본다 —
# module_from_spec만으로는 sys.modules에 등록되지 않아 그 조회가 None이 되어 깨진다.
# exec_module 전에 등록해야 한다.
sys.modules[_spec.name] = models
_spec.loader.exec_module(models)

CODE_LENGTH = 6
CODE_TTL = timedelta(minutes=10)
MAX_WRONG_ATTEMPTS = 5
WRONG_ATTEMPT_COOLDOWN = timedelta(minutes=10)
# 00-core.md/account-link.md: "플레이어당 최대 계정 수"의 범위는 "전역"이다. 3단계
# settings 서비스의 범위는 플레이어/서버뿐이라(core/settings_slash.py 참고) "서버와
# 무관하게 봇 전체에 하나"인 값을 표현하지 못한다. 설계서에 없는 세부 판단으로, 이
# 값은 설정이 아니라 코드 상수로 둔다.
MAX_LINKED_ACCOUNTS = 3


def _generate_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(CODE_LENGTH))


@slash_only("연결 시작")
async def start_link(ctx: ToolContext) -> ToolResult:
    """일회용 연결 코드를 발급한다."""
    accounts = await ctx.players.accounts_for(ctx.player_id)
    if len(accounts) >= MAX_LINKED_ACCOUNTS:
        return ToolResult(error=f"플레이어당 계정은 최대 {MAX_LINKED_ACCOUNTS}개까지 연결할 수 있다")

    # 이 명령을 "실제로 실행한" 계정이 발급자다. 플레이어가 이미 계정을 여러 개 연결했다면
    # accounts_for(ctx.player_id)의 첫 번째(가장 먼저 연결한 계정)는 지금 명령을 친 계정과
    # 다를 수 있다 — ctx.discord_id(core/slash.py의 _dispatch가 interaction.user.id를 그대로
    # 실어 준다)만이 "지금 이 사람"이다.
    if ctx.discord_id is None:
        return ToolResult(error="계정 식별에 실패했다. 다시 시도해 주세요")

    code = _generate_code()
    assert ctx.db is not None
    await models.create_code(
        ctx.db, code=code, issuer_player_id=ctx.player_id, issuer_discord_id=ctx.discord_id,
        now=datetime.now(UTC), ttl=CODE_TTL,
    )
    return ToolResult(
        data={"code": code},
        display=Display(
            content=f"연결 코드: `{code}` (10분간 유효). 연결할 계정에서 `/연결 참여`에 입력해 주세요.",
            ephemeral=True,  # 코드를 같은 채널의 다른 사람이 보고 먼저 참여해 버리면 안 된다.
        ),
    )


async def _proceed_to_mutual_confirm(ctx: ToolContext, link_code: Any, joiner_discord_id: str) -> ToolResult:
    assert ctx.db is not None
    await models.mark_joined(ctx.db, link_code.code, joiner_discord_id=joiner_discord_id)

    issuer_view = _ConfirmButtonsView(code=link_code.code, side="issuer", ctx=ctx)
    await ctx.notify.dm(
        link_code.issuer_player_id,
        f"<@{joiner_discord_id}>이 연결을 요청했다. 같은 사람인가요?",
        view=issuer_view,
    )

    joiner_view = _ConfirmButtonsView(code=link_code.code, side="joiner", ctx=ctx)
    return ToolResult(
        data={"status": "pending_confirmation"},
        display=Display(content=f"<@{link_code.issuer_discord_id}>과 연결해요. 맞나요?", view=joiner_view),
    )


@slash_only("연결 참여")
async def join_link(ctx: ToolContext, code: str) -> ToolResult:
    """코드를 넣어 연결을 요청한다."""
    assert ctx.db is not None
    # start_link와 같은 이유로, 참여도 "지금 이 명령을 친" 계정 기준이다 — 플레이어의 다른
    # 연결된 계정이 아니라 ctx.discord_id 그 자체를 옮긴다.
    joiner_discord_id = ctx.discord_id
    if joiner_discord_id is None:
        return ToolResult(error="계정 식별에 실패했다. 다시 시도해 주세요")

    now = datetime.now(UTC)
    if await models.wrong_attempt_count(ctx.db, joiner_discord_id, now=now) >= MAX_WRONG_ATTEMPTS:
        return ToolResult(error="너무 많이 틀렸다. 잠시 후 다시 시도해 주세요")

    link_code = await models.get_code(ctx.db, code)
    if link_code is None or link_code.expires_at <= now.isoformat():
        await models.record_wrong_attempt(
            ctx.db, joiner_discord_id, now=now, lock_after=MAX_WRONG_ATTEMPTS, cooldown=WRONG_ATTEMPT_COOLDOWN
        )
        return ToolResult(error="코드가 틀렸거나 만료됐다")

    if link_code.status != "pending":
        # 이미 다른 사람이 참여해 'joined' 이상으로 넘어간 코드다 — 코드가 유출돼 두 번째
        # 참여자가 끼어들어도 먼저 참여한 쪽의 진행을 덮어쓰지 않는다(중복 소비 방지).
        return ToolResult(error="이미 다른 사람이 참여한 코드다")

    if link_code.issuer_player_id == ctx.player_id:
        return ToolResult(error="이미 같은 플레이어다")

    await models.reset_wrong_attempts(ctx.db, joiner_discord_id)

    counts = await ctx.player_data.count(ctx.player_id) if ctx.player_data is not None else {}
    if counts:
        view = _DiscardConfirmView(code=code, joiner_discord_id=joiner_discord_id, ctx=ctx)
        lines = "\n".join(f"- {table}: {n}건" for table, n in counts.items())
        return ToolResult(
            data={"counts": counts},
            display=Display(
                content=f"이 계정에 이미 데이터가 있다:\n{lines}\n버리고 연결할까요?", view=view, ephemeral=True
            ),
        )

    return await _proceed_to_mutual_confirm(ctx, link_code, joiner_discord_id)


class _DiscardConfirmView(discord.ui.View):
    """참여자 계정에 이미 데이터가 있을 때만 뜬다.

    여기서 "버리고 연결"을 눌러도 아직 아무것도 지우지 않는다 — 양쪽(발급자/참여자)이 모두
    확인해야 실제로 연결되고, 그 때 `_ConfirmButtonsView.confirm`이 한 번에 지운다. 그래야
    코드가 만료되거나 상대가 끝내 확인하지 않아도 참여자의 데이터가 멋대로 사라지는 일이
    없다. 취소하면 정말 아무것도 바뀌지 않는다."""

    def __init__(self, *, code: str, joiner_discord_id: str, ctx: ToolContext) -> None:
        super().__init__(timeout=300)
        self._code = code
        self._joiner_discord_id = joiner_discord_id
        self._ctx = ctx

    def _is_requester(self, interaction: discord.Interaction) -> bool:
        return str(interaction.user.id) == self._joiner_discord_id

    @discord.ui.button(label="버리고 연결", style=discord.ButtonStyle.danger)
    async def discard_and_continue(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        if not self._is_requester(interaction):
            await interaction.response.send_message("이건 당신의 확인 요청이 아니다", ephemeral=True)
            return

        assert self._ctx.db is not None
        link_code = await models.get_code(self._ctx.db, self._code)
        if link_code is None or link_code.expires_at <= datetime.now(UTC).isoformat():
            await interaction.response.send_message("코드가 만료됐다", ephemeral=True)
            return
        if link_code.status != "pending":
            # 이 버튼이 뜬 뒤 — 같은 코드로 다른 사람이 먼저 참여를 끝냈을 수 있다
            # (join_link은 아직 mark_joined를 부르지 않았으므로, 이 상태가 바뀌었다면
            # 그건 다른 참여자다).
            await interaction.response.send_message("이미 다른 사람이 참여한 코드다", ephemeral=True)
            return

        # 아직 아무것도 지우지 않는다 — "버리는 데 동의했다"는 표시만 남기고, 실제로 지우는
        # 일은 양쪽이 다 확인했을 때 한 번만 일어난다(_ConfirmButtonsView.confirm).
        await models.mark_discard_agreed(self._ctx.db, self._code)

        result = await _proceed_to_mutual_confirm(self._ctx, link_code, self._joiner_discord_id)
        await interaction.response.send_message(**render_result(result))

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        if not self._is_requester(interaction):
            await interaction.response.send_message("이건 당신의 확인 요청이 아니다", ephemeral=True)
            return
        await interaction.response.send_message("취소했다. 아무것도 바뀌지 않았다.", ephemeral=True)


class _ConfirmButtonsView(discord.ui.View):
    """발급자/참여자 양쪽에 각각 뜬다. 둘 다 눌러야 연결이 끝난다."""

    def __init__(self, *, code: str, side: str, ctx: ToolContext) -> None:
        super().__init__(timeout=int(CODE_TTL.total_seconds()))
        self._code = code
        self._side = side
        self._ctx = ctx

    @discord.ui.button(label="확인", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        assert self._ctx.db is not None
        link_code = await models.get_code(self._ctx.db, self._code)
        if link_code is None or link_code.expires_at <= datetime.now(UTC).isoformat():
            await interaction.response.send_message("만료된 요청이다", ephemeral=True)
            return
        if link_code.status == "completed":
            # 이미 끝난 코드로 다시 눌렀다(두 번 클릭 등) — link_account를 또 부르면
            # player_discord_accounts.discord_id 유니크 제약으로 터진다. 조용히 안내만 한다.
            await interaction.response.send_message("이미 연결이 끝났다", ephemeral=True)
            return

        expected = link_code.issuer_discord_id if self._side == "issuer" else link_code.joiner_discord_id
        if str(interaction.user.id) != expected:
            await interaction.response.send_message("이건 당신의 확인 요청이 아니다", ephemeral=True)
            return

        await models.set_confirmed(self._ctx.db, self._code, side=self._side)
        refreshed = await models.get_code(self._ctx.db, self._code)
        assert refreshed is not None

        if refreshed.issuer_confirmed and refreshed.joiner_confirmed:
            assert refreshed.joiner_discord_id is not None
            # 파괴적인 동작(데이터 삭제, 계정 재배치)은 여기, 양쪽이 다 확인한 이 순간에만
            # 일어난다 — 버리고 연결 확인이든, 데이터 없음 직행 경로든 마찬가지다.
            if refreshed.discard_agreed and self._ctx.player_data is not None:
                stale_joiner = await self._ctx.players.player_for_discord(refreshed.joiner_discord_id)
                if stale_joiner is not None:
                    await self._ctx.player_data.delete(stale_joiner.id)
            # 참여자 계정은 아직 자기 자신의(또는 다른) 플레이어에 연결되어 있을 수 있다 —
            # player_discord_accounts.discord_id는 유니크라 그대로 link_account를 부르면
            # 깨진다. unlink_account는 그 계정 하나만 떼어내고, 원래 플레이어의 다른 계정과
            # 태그는 전혀 건드리지 않는다(dissolve_player와 달리 — 그 플레이어가 여러 계정을
            # 갖고 있었다면 나머지는 그대로 남아야 한다). 이미 떼어져 있으면 조용히 넘어간다.
            await self._ctx.players.unlink_account(refreshed.joiner_discord_id)
            await self._ctx.players.link_account(refreshed.issuer_player_id, refreshed.joiner_discord_id)
            await models.mark_status(self._ctx.db, self._code, status="completed")
            await self._ctx.emit(
                "players.linked",
                {
                    "player_id": refreshed.issuer_player_id,
                    "discord_id": refreshed.joiner_discord_id,
                    "action": "linked",
                },
            )
            await self._ctx.notify.dm(refreshed.issuer_player_id, "연결이 완료됐다.")
            await interaction.response.send_message("연결이 완료됐다!", ephemeral=True)
        else:
            await models.mark_status(self._ctx.db, self._code, status=f"{self._side}_confirmed")
            await interaction.response.send_message("확인했다. 상대의 확인을 기다리는 중이다.", ephemeral=True)


@slash_only("연결 목록")
async def list_links(ctx: ToolContext) -> ToolResult:
    """연결된 계정, 대표 여부, 연결일을 본인에게만 보여준다."""
    accounts = await ctx.players.accounts_for(ctx.player_id)
    lines = [f"<@{a.discord_id}>{' (대표)' if a.is_primary else ''} — {a.linked_at}" for a in accounts]
    return ToolResult(
        data={"accounts": [a.discord_id for a in accounts]},
        display=Display(content="\n".join(lines) if lines else "연결된 계정이 없다."),
    )


@slash_only("연결 대표")
async def set_primary(ctx: ToolContext, discord_id: str) -> ToolResult:
    """대표 계정을 바꾼다."""
    try:
        await ctx.players.set_primary(ctx.player_id, discord_id)
    except ValueError as exc:
        return ToolResult(error=str(exc))
    return ToolResult(data={"primary": discord_id})


@slash_only("연결 해제")
async def unlink(ctx: ToolContext, discord_id: str) -> ToolResult:
    """연결을 푼다. 대표 계정은 대표를 넘긴 뒤에만 뗄 수 있다."""
    player = await ctx.players.get_player(ctx.player_id)
    if player is None:
        return ToolResult(error="모르는 플레이어다")
    if player.primary_discord_id == discord_id:
        accounts = await ctx.players.accounts_for(ctx.player_id)
        if len(accounts) > 1:
            return ToolResult(error="대표 계정은 먼저 다른 계정으로 대표를 넘긴 뒤에 뗄 수 있다")

    await ctx.players.unlink_account(discord_id)
    await ctx.emit("players.linked", {"player_id": ctx.player_id, "discord_id": discord_id, "action": "unlinked"})
    return ToolResult(data={"unlinked": discord_id})
