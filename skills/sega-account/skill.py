# skills/sega-account/skill.py
from __future__ import annotations

import logging
from typing import Any

import discord
import httpx
from chuni_penguin.adapters.errors import (
    AuthenticationError,
    HTTPError,
    NoCardsRegistered,
)

from core.decorators import on, slash_only, tool
from core.tool_types import Display, GameRef, ToolContext, ToolResult, UserFacingError
from services.game.registry import profile_for

logger = logging.getLogger(__name__)

_notified_expiries: set[str] = set()


_BAD_CREDENTIALS = (
    "로그인에 실패했어. ID와 비밀번호가 맞는지 확인해 줘. "
    "(OTP(2단계 인증)를 쓰는 계정은 아직 이 방법으로 로그인할 수 없어.)"
)
_NO_CARDS = "이 SEGA 계정에 등록된 Aime 카드(액세스 코드)가 없어. https://my-aime.net 에서 먼저 등록해 줘."
_UNKNOWN_FAILURE = "로그인에 실패했어. 잠시 뒤에 다시 해 줘."


def _login_order(ctx: ToolContext) -> list[str]:
    """먼저 요청한 게임(없으면 주 게임)의 NET으로, 그 NET이 점검이거나 닿지 않으면 다른 게임의 NET으로 로그인한다
    (2026-11-22 사용자 결정 — CHUNITHM-NET 점검 중에 maimai는 정상인데 로그인이 막혔다). `clal`은 게임 공통이다."""
    sega_games = [game for game in ctx.game_services if profile_for(game).account == "sega"]  # Arcaea는 계정이 따로다
    first = [ctx.game] if profile_for(ctx.game).account == "sega" else []
    return [*first, *(game for game in sega_games if game not in first)]


async def _attempt_login(ctx: ToolContext, username: str, password: str, interaction: discord.Interaction) -> None:
    """실제 로그인 호출과 성공/실패 안내를 한 곳에 모은다. 처음 로그인하는 경로와 "덮어쓰기" 확인 버튼 경로가 같이 쓴다.
    예외 메시지는 사용자에게 보이지 않는다 — 비밀번호나 응답 본문의 일부가 섞여 나갈 수 있다(CLAUDE.md 비밀 값 규칙).
    로그에도 예외 클래스 이름만 남긴다."""
    unavailable: list[str] = []
    for game in _login_order(ctx):
        net_name = profile_for(game).net_name
        try:
            profile = await ctx.for_game(game).chunithm_net.login_with_credentials(ctx.player_id, username, password, ctx)
        except NoCardsRegistered:
            await interaction.followup.send(_NO_CARDS, ephemeral=True)
            return
        except AuthenticationError:
            logger.warning("sega login failed for player %s on %s: AuthenticationError", ctx.player_id, game)
            await interaction.followup.send(_BAD_CREDENTIALS, ephemeral=True)
            return
        except (HTTPError, httpx.TransportError, UserFacingError) as error:
            # 점검(503)·서버 오류·접속 장애·NET 오류 페이지 — 그 NET만의 문제라 다른 게임 NET으로 다시 해 본다.
            logger.warning("sega login: %s unavailable for player %s: %s", game, ctx.player_id, type(error).__name__)
            unavailable.append(net_name)
            continue
        except Exception as error:  # noqa: BLE001 - 어떤 예외든 같은 안내만 준다(위 docstring)
            logger.warning("sega login failed for player %s on %s: %s", ctx.player_id, game, type(error).__name__)
            await interaction.followup.send(_UNKNOWN_FAILURE, ephemeral=True)
            return
        _notified_expiries.discard(ctx.player_id)  # 다시 로그인했으니 다음 만료는 또 안내해야 한다.
        note = f" {'·'.join(unavailable)}이 점검 중이거나 닿지 않아 {net_name}으로 로그인했어." if unavailable else ""
        await interaction.followup.send(
            f"로그인 완료! ({profile.username}){note} `/동기화`로 기록을 가져와 줘.", ephemeral=True
        )
        return
    await interaction.followup.send(
        f"지금 {'·'.join(unavailable)}이 모두 점검 중이거나 닿지 않아 로그인하지 못했어. 잠시 뒤에 다시 해 줘.", ephemeral=True
    )


class _OverwriteConfirmView(discord.ui.View):
    """이미 다른 계정으로 연결돼 있을 때만 뜬다. 제출된 ID/비밀번호는 이 뷰 인스턴스의
    메모리에만 있고, "덮어쓰기"를 눌렀을 때 실제 로그인에만 쓴다 — 로그에도, 메시지
    내용에도 절대 넣지 않는다(CLAUDE.md 비밀 값 규칙)."""

    def __init__(self, *, ctx: ToolContext, username: str, password: str) -> None:
        super().__init__(timeout=120)
        self._ctx = ctx
        self._username = username
        self._password = password

    @discord.ui.button(label="덮어쓰기", style=discord.ButtonStyle.danger)
    async def overwrite(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        await interaction.response.defer(ephemeral=True)
        await _attempt_login(self._ctx, self._username, self._password, interaction)

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("취소했어.", ephemeral=True)


_LOGIN_NOTICE = (
    "**입력한 내용은 안전해?**\n"
    "- ID와 비밀번호는 저장하지 않아. 로그인하는 데 한 번 쓰고 바로 버려.\n"
    "- 로그인에 성공하면 받은 세션 쿠키(clal)만 암호화해서 보관하고, 암호화 키는 서버 안에만 있어.\n"
    "- 이 창에 쓴 내용은 채널에 남지 않고 다른 사람에게 보이지 않아. 나(AI)에게도 전달되지 않아.\n"
    "- 로그아웃하면 보관한 쿠키도 지워."
)


class _LoginModal(discord.ui.Modal, title="SEGA 로그인"):
    def __init__(self, *, ctx: ToolContext) -> None:
        super().__init__()
        self._ctx = ctx
        # 입력칸은 Label로 감싸야 안내 글(TextDisplay)과 같은 창에 둘 수 있다.
        self.username_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            placeholder="CHUNITHM-NET·maimai DX NET에 로그인하는 SEGA ID", required=True, max_length=64
        )
        self.password_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            placeholder="OTP(2단계 인증)를 쓰는 계정은 아직 안 돼", required=True, max_length=128
        )
        self.add_item(discord.ui.TextDisplay(_LOGIN_NOTICE))
        self.add_item(discord.ui.Label(text="SEGA ID", component=self.username_input))
        self.add_item(discord.ui.Label(text="비밀번호", component=self.password_input))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # defer 뒤 followup으로만 답한다 — 입력값은 채널에 남지 않고, 여기서도 다시 보여주지 않는다.
        await interaction.response.defer(ephemeral=True)
        username = str(self.username_input.value)
        password = str(self.password_input.value)

        status = await self._ctx.chunithm_net.status(self._ctx.player_id)
        if status.linked:
            # CHUNITHM-NET 계정은 플레이어당 하나다. 다른 계정으로 다시 로그인하면 덮어쓸지
            # 먼저 확인받는다(docs/design/skills/sega-account.md) — 여기서는 아직 로그인을
            # 부르지 않는다.
            view = _OverwriteConfirmView(ctx=self._ctx, username=username, password=password)
            await interaction.followup.send(
                f"이미 '{status.nickname}' 계정으로 로그인돼 있어. 새 계정으로 덮어쓸까?",
                view=view,
                ephemeral=True,
            )
            return

        await _attempt_login(self._ctx, username, password, interaction)


@slash_only("로그인 세가")
async def start_login(ctx: ToolContext, game: GameRef | None = None) -> discord.ui.Modal:
    """SEGA ID/비밀번호를 입력하는 창을 연다. 입력값은 채널에 남지 않고, 로그인에
    성공하면 거기서 나온 clal 쿠키만 저장한다 — ID/비밀번호는 저장하지 않는다.
    game(비우면 주 게임)의 NET으로 먼저 로그인하고, 그 NET이 점검이면 다른 게임 NET으로 한다(쿠키는 게임 공통)."""
    return _LoginModal(ctx=ctx)


@tool()
async def status(ctx: ToolContext) -> ToolResult:
    """로그인 여부, 게임별 NET 이름·마지막 동기화 시각, 기록 공개 설정을 보여준다(로그인은 두 게임 공통)."""
    login_status = await ctx.chunithm_net.status(ctx.player_id)
    player = await ctx.players.get_player(ctx.player_id)
    assert player is not None
    # 게임별 서비스 묶음이 없으면(시험 등) 지금 게임 하나만 본다.
    games = [game for game in ctx.game_services if profile_for(game).account == "sega"] or [ctx.game]
    per_game: dict[str, dict[str, str | None]] = {}
    for game in games:
        records = ctx.for_game(game).records
        profile = await records.player_profile(ctx.player_id)
        per_game[game] = {
            "username": profile.username if profile is not None else None,
            "last_synced_at": await records.last_sync_at(ctx.player_id),
        }

    data = {
        "linked": login_status.linked,
        "login_nickname": login_status.nickname,
        "games": per_game,
        "records_public": player.records_public,
    }
    if login_status.linked:
        lines = [f"로그인: 되어있음 ({login_status.nickname})" if login_status.nickname else "로그인: 되어있음"]
    else:
        lines = ["로그인: 안 되어있음"]
    for game, info in per_game.items():
        if info["last_synced_at"] is None:
            lines.append(f"{profile_for(game).label}: 아직 동기화한 적 없음")
        else:
            name = info["username"] or login_status.nickname or "이름 모름"
            lines.append(f"{profile_for(game).label}: {name}, 마지막 동기화 {info['last_synced_at']}")
    lines.append(f"기록 공개: {'공개' if player.records_public else '비공개'}")
    return ToolResult(
        data=data, display=Display(content="\n".join(lines), ephemeral=True), display_mode="optional"
    )


@tool(tags_any=["sega-linked", "arcaea-linked"], mutates=True)
async def set_privacy(ctx: ToolContext, public: bool) -> ToolResult:
    """기록(프로필, 베스트, 곡별 기록) 공개 여부를 켜거나 끈다."""
    await ctx.players.set_visibility(ctx.player_id, "records_public", public)
    message = "이제 다른 사람도 이 기록을 볼 수 있어." if public else "이제 이 기록은 너만 볼 수 있어."
    return ToolResult(
        data={"records_public": public}, display=Display(content=message, ephemeral=True), display_mode="optional"
    )


@tool(tags=["sega-linked"], mutates=True, confirm="always")
async def logout(ctx: ToolContext) -> ToolResult:
    """저장된 clal 쿠키를 지운다. 동기화된 기록은 남는다. "로그아웃"·"세가 로그아웃"·"쿠키 지워"처럼 분명히 말했을 때만
    부른다 — 멘션에서는 확인 없이 바로 실행된다. "계정 끊어줘"·"연결 해제"처럼 무엇을 끊는지 애매하면 부르지 말고 묻는다."""
    await ctx.chunithm_net.logout(ctx.player_id, ctx)
    return ToolResult(data={"logged_out": True}, display=Display(content="로그아웃했어. 보관하던 쿠키는 지웠고, 모아 둔 기록은 그대로 남아 있어.", ephemeral=True))


@on("sega.login.expired")
async def on_login_expired(ctx: ToolContext, payload: dict[str, object]) -> None:
    """만료된 로그인에 대해 대표 계정에 재로그인을 한 번만 안내한다(이 프로세스가 켜져
    있는 동안만 — 재시작하거나 다시 로그인하면 다시 보낸다. 이 이벤트는 core/events.py의 EventBus가 이
    스킬의 manifest.services만 담은 실제 ctx와 함께 호출한다 — 배선은 끝났다)."""
    player_id = str(payload["player_id"])
    if player_id in _notified_expiries:
        return
    await ctx.notify.dm(player_id, "CHUNITHM-NET 로그인이 풀렸어. `/로그인 세가`로 다시 로그인해 줘.")
    _notified_expiries.add(player_id)  # 보낸 뒤에 적는다 — DM이 막혀 실패하면 다음 만료 때 다시 시도한다.
