# skills/chunithm-account/skill.py
from __future__ import annotations

from typing import Any

import discord

from core.decorators import on, slash_only, tool
from core.tool_types import Display, ToolContext, ToolResult

_notified_expiries: set[str] = set()


async def _attempt_login(ctx: ToolContext, clal: str, interaction: discord.Interaction) -> None:
    """실제 login() 호출과 성공/실패 안내를 한 곳에 모은다. 처음 로그인하는 경로와
    "덮어쓰기" 확인 버튼 경로가 이 함수를 같이 쓴다 — try/except를 두 번 베끼지 않는다."""
    try:
        profile = await ctx.chunithm_net.login(ctx.player_id, clal, ctx)
    except Exception:  # noqa: BLE001 - 어떤 예외든 같은 안내만 준다. 원인 예외를 그대로
        # 보여주면 clal이나 응답 본문의 일부가 메시지에 섞여 나갈 수 있다(CLAUDE.md 비밀 값 규칙).
        await interaction.followup.send(
            "로그인에 실패했다. clal 값이 올바른지, 만료되지 않았는지 확인해 달라.", ephemeral=True
        )
        return
    await interaction.followup.send(f"로그인 완료! ({profile.username})", ephemeral=True)


class _OverwriteConfirmView(discord.ui.View):
    """이미 다른 계정으로 연결돼 있을 때만 뜬다. 제출된 clal 값은 이 뷰 인스턴스의 메모리에만
    있고, "덮어쓰기"를 눌렀을 때 실제 login()을 부르는 데만 쓴다 — 로그에도, 메시지 내용에도
    절대 넣지 않는다(CLAUDE.md 비밀 값 규칙)."""

    def __init__(self, *, ctx: ToolContext, clal: str) -> None:
        super().__init__(timeout=120)
        self._ctx = ctx
        self._clal = clal

    @discord.ui.button(label="덮어쓰기", style=discord.ButtonStyle.danger)
    async def overwrite(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        await interaction.response.defer(ephemeral=True)
        await _attempt_login(self._ctx, self._clal, interaction)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("취소했다.", ephemeral=True)


class _LoginModal(discord.ui.Modal, title="CHUNITHM-NET 로그인"):
    clal_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="clal 쿠키 값",
        style=discord.TextStyle.paragraph,
        placeholder="chunithm-net-eng.com에 로그인한 뒤 개발자 도구에서 복사한 clal=... 값",
        required=True,
        max_length=4000,
    )

    def __init__(self, *, ctx: ToolContext) -> None:
        super().__init__()
        self._ctx = ctx

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # defer 뒤 followup으로만 답한다 — 입력값은 채널에 남지 않고, 여기서도 다시 보여주지 않는다.
        await interaction.response.defer(ephemeral=True)
        clal = str(self.clal_input.value)

        status = await self._ctx.chunithm_net.status(self._ctx.player_id)
        if status.linked:
            # CHUNITHM-NET 계정은 플레이어당 하나다. 다른 계정으로 다시 로그인하면 덮어쓸지
            # 먼저 확인받는다(docs/design/skills/chunithm-account.md) — 여기서는 아직 login()을
            # 부르지 않는다.
            view = _OverwriteConfirmView(ctx=self._ctx, clal=clal)
            await interaction.followup.send(
                f"이미 '{status.nickname}'(으)로 로그인되어 있다. 새 계정으로 덮어쓸까?",
                view=view,
                ephemeral=True,
            )
            return

        await _attempt_login(self._ctx, clal, interaction)


@slash_only("로그인")
async def start_login(ctx: ToolContext) -> discord.ui.Modal:
    """clal 쿠키를 입력하는 창을 연다. 입력값은 채널에 남지 않는다."""
    return _LoginModal(ctx=ctx)


@tool(slash="계정")
async def status(ctx: ToolContext) -> ToolResult:
    """로그인 여부, CHUNITHM-NET 닉네임, 마지막 동기화 시각, 기록 공개 설정을 보여준다."""
    login_status = await ctx.chunithm_net.status(ctx.player_id)
    player = await ctx.players.get_player(ctx.player_id)
    assert player is not None
    last_synced_at = await ctx.records.last_sync_at(ctx.player_id)

    data = {
        "linked": login_status.linked,
        "nickname": login_status.nickname,
        "last_synced_at": last_synced_at,
        "records_public": player.records_public,
    }
    lines = [
        f"로그인: {'되어있음' if login_status.linked else '안 되어있음'}",
        f"닉네임: {login_status.nickname or '없음'}",
        f"마지막 동기화: {last_synced_at or '아직 동기화한 적 없음'}",
        f"기록 공개: {'공개' if player.records_public else '비공개'}",
    ]
    return ToolResult(
        data=data, display=Display(content="\n".join(lines), ephemeral=True), display_mode="optional"
    )


@tool(slash="기록공개", tags=["chunithm-linked"], mutates=True)
async def set_privacy(ctx: ToolContext, public: bool) -> ToolResult:
    """기록(프로필, 베스트, 곡별 기록) 공개 여부를 켜거나 끈다."""
    await ctx.players.set_visibility(ctx.player_id, "records_public", public)
    message = "이제 다른 사람도 내 기록을 볼 수 있다." if public else "내 기록은 이제 나만 볼 수 있다."
    return ToolResult(
        data={"records_public": public}, display=Display(content=message, ephemeral=True), display_mode="optional"
    )


@tool(slash="로그아웃", tags=["chunithm-linked"], mutates=True, confirm="always")
async def logout(ctx: ToolContext) -> ToolResult:
    """저장된 clal 쿠키를 지운다. 동기화된 기록은 남는다."""
    await ctx.chunithm_net.logout(ctx.player_id, ctx)
    return ToolResult(data={"logged_out": True}, display=Display(content="로그아웃했다.", ephemeral=True))


@on("chunithm.login.expired")
async def on_login_expired(ctx: ToolContext, payload: dict[str, object]) -> None:
    """만료된 로그인에 대해 대표 계정에 재로그인을 한 번만 안내한다(이 프로세스가 켜져
    있는 동안만 — 재시작하면 다시 보낸다. core/bot.py가 이 이벤트를 실제로 ctx와 함께
    호출하도록 배선하는 일은 7단계 chunithm-sync에서 마저 한다 — 결정 사항 2번 참고)."""
    player_id = str(payload["player_id"])
    if player_id in _notified_expiries:
        return
    _notified_expiries.add(player_id)
    await ctx.notify.dm(player_id, "로그인이 풀렸다. `/로그인`으로 다시 넣어 달라.")
