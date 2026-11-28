# skills/arcaea-account/skill.py
"""Arcaea Online 로그인(19단계 2단계, 2026-11-28 사용자 결정 — 아이디·비밀번호와 쿠키 직접 넣기 둘 다)."""
from __future__ import annotations

import logging

import discord
import httpx
from chuni_penguin.adapters.errors import AuthenticationError

from core.decorators import on, slash_only, tool
from core.tool_types import Display, ToolContext, ToolResult, UserFacingError

logger = logging.getLogger(__name__)

_notified_expiries: set[str] = set()

_BAD_CREDENTIALS = "로그인에 실패했어. 아이디(또는 이메일)와 비밀번호가 맞는지 확인해 줘."
_BAD_COOKIE = "그 쿠키로는 Arcaea Online에 들어가지 못했어. 로그인한 브라우저에서 `Cookie:` 값을 다시 통째로 복사해 줘."
_NEED_ONE = "아이디·비밀번호 둘 다 넣거나, 쿠키만 넣어 줘."
_UNKNOWN_FAILURE = "로그인에 실패했어. 잠시 뒤에 다시 해 줘."

_LOGIN_NOTICE = (
    "**아이디·비밀번호** 또는 **쿠키** 중 하나만 넣으면 돼. 구글·애플로 가입해 비밀번호가 없으면 쿠키를 넣어 줘.\n"
    "- 아이디·비밀번호는 저장하지 않아. 로그인하는 데 한 번 쓰고 바로 버려.\n"
    "- 로그인으로 받은 세션 쿠키(sid)만 암호화해서 보관하고, 로그아웃하면 지워.\n"
    "- 이 창에 쓴 내용은 채널에 남지 않고 나(AI)에게도 전달되지 않아.\n"
    "- 기록은 Arcaea Online 구독자만 가져올 수 있어."
)


async def _attempt_login(ctx: ToolContext, interaction: discord.Interaction, *, username: str, password: str,
                         cookie: str) -> None:
    """예외 메시지는 사용자에게 보이지 않는다 — 비밀번호나 쿠키가 섞여 나갈 수 있다. 로그에도 예외 클래스 이름만 남긴다."""
    try:
        if cookie:
            profile = await ctx.chunithm_net.login_with_cookie(ctx.player_id, cookie)
        else:
            profile = await ctx.chunithm_net.login_with_credentials(ctx.player_id, username, password)
    except AuthenticationError:
        logger.warning("arcaea login failed for player %s: AuthenticationError", ctx.player_id)
        await interaction.followup.send(_BAD_COOKIE if cookie else _BAD_CREDENTIALS, ephemeral=True)
        return
    except UserFacingError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    except (httpx.TransportError, Exception) as error:  # noqa: BLE001 - 어떤 예외든 같은 안내만 준다(위 docstring)
        logger.warning("arcaea login failed for player %s: %s", ctx.player_id, type(error).__name__)
        await interaction.followup.send(_UNKNOWN_FAILURE, ephemeral=True)
        return
    _notified_expiries.discard(ctx.player_id)
    name = f" ({profile.username})" if profile.username else ""
    await interaction.followup.send(
        f"Arcaea 로그인 완료!{name} `/동기화 game:아르케아`로 기록을 가져와 줘.", ephemeral=True
    )


class _LoginModal(discord.ui.Modal, title="Arcaea 로그인"):
    def __init__(self, *, ctx: ToolContext) -> None:
        super().__init__()
        self._ctx = ctx
        self.username_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            placeholder="아이디 또는 이메일", required=False, max_length=128
        )
        self.password_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            placeholder="비밀번호", required=False, max_length=128
        )
        self.cookie_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            placeholder="비밀번호가 없으면: 브라우저의 Cookie: 값을 통째로", required=False, max_length=4000,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(discord.ui.TextDisplay(_LOGIN_NOTICE))
        self.add_item(discord.ui.Label(text="아이디", component=self.username_input))
        self.add_item(discord.ui.Label(text="비밀번호", component=self.password_input))
        self.add_item(discord.ui.Label(text="쿠키", component=self.cookie_input))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        username = str(self.username_input.value).strip()
        password = str(self.password_input.value)
        cookie = str(self.cookie_input.value).strip()
        if not cookie and not (username and password):
            await interaction.followup.send(_NEED_ONE, ephemeral=True)
            return
        await _attempt_login(self._ctx, interaction, username=username, password=password, cookie=cookie)


@slash_only("아르케아 로그인")
async def start_login(ctx: ToolContext) -> discord.ui.Modal:
    """Arcaea 아이디·비밀번호나 쿠키를 넣는 창을 연다. 입력값은 채널에 남지 않고, 로그인에 성공하면 세션 쿠키(sid)만
    저장한다 — 아이디·비밀번호는 저장하지 않는다. 이미 로그인돼 있으면 새 계정으로 덮어쓴다."""
    return _LoginModal(ctx=ctx)


@tool()
async def status(ctx: ToolContext) -> ToolResult:
    """Arcaea Online 로그인 여부, 이름, 마지막 동기화 시각을 보여준다."""
    login = await ctx.chunithm_net.status(ctx.player_id)
    last_synced_at = await ctx.records.last_sync_at(ctx.player_id)
    lines = [f"Arcaea 로그인: 되어있음 ({login.nickname})" if login.linked and login.nickname
             else ("Arcaea 로그인: 되어있음" if login.linked else "Arcaea 로그인: 안 되어있음")]
    lines.append(f"마지막 동기화: {last_synced_at}" if last_synced_at else "아직 동기화한 적 없음")
    return ToolResult(
        data={"linked": login.linked, "nickname": login.nickname, "last_synced_at": last_synced_at},
        display=Display(content="\n".join(lines), ephemeral=True), display_mode="optional",
    )


@tool(tags=["arcaea-linked"], mutates=True, confirm="always")
async def logout(ctx: ToolContext) -> ToolResult:
    """저장한 Arcaea 쿠키를 지운다. 동기화된 기록은 남는다. "아르케아 로그아웃"처럼 분명히 말했을 때만 부른다."""
    await ctx.chunithm_net.logout(ctx.player_id, ctx)
    return ToolResult(
        data={"logged_out": True},
        display=Display(content="Arcaea 로그아웃했어. 보관하던 쿠키는 지웠고, 모아 둔 기록은 그대로 남아 있어.", ephemeral=True),
    )


@on("arcaea.login.expired")
async def on_login_expired(ctx: ToolContext, payload: dict[str, object]) -> None:
    """만료된 로그인에 대해 재로그인을 한 번만 안내한다(이 프로세스가 켜져 있는 동안만)."""
    player_id = str(payload["player_id"])
    if player_id in _notified_expiries:
        return
    await ctx.notify.dm(player_id, "Arcaea Online 로그인이 풀렸어. `/아르케아 로그인`으로 다시 로그인해 줘.")
    _notified_expiries.add(player_id)
