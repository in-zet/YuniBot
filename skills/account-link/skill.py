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


async def _proceed_to_mutual_confirm(
    ctx: ToolContext, link_code: Any, joiner_discord_id: str, *, discard_agreed: bool = False
) -> ToolResult:
    assert ctx.db is not None
    # mark_joined는 WHERE status = 'pending'까지 포함한 원자적 UPDATE다 — 호출자(join_link의
    # 직행 경로, discard_and_continue 둘 다)가 이미 "status가 pending"임을 확인했더라도, 그
    # 확인과 이 호출 사이에 다른 참여자가 먼저 끝냈을 수 있다(두 await 사이의 TOCTOU). 그래서
    # 반환값을 반드시 확인한다 — False면 이 호출은 아무것도 바꾸지 못했다는 뜻이다. discard_agreed도
    # 이 같은 호출에 함께 실어, "버리는 데 동의했다"는 표시가 실제로 이 전이를 가져간 참여자와
    # 분리될 수 없게 한다(같은 유출된 코드로 다른 참여자가 경쟁해도 섞이지 않는다).
    if not await models.mark_joined(
        ctx.db, link_code.code, joiner_discord_id=joiner_discord_id, discard_agreed=discard_agreed
    ):
        return ToolResult(error="이미 다른 사람이 참여한 코드다")

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

    # player_data는 계정이 아니라 플레이어에 딸린다. 참여자가 이미 계정을 2개 이상 연결했다면
    # "버릴 데이터"가 지금 쓴 계정의 것인지, 다른 계정의 것인지 구분할 길이 없다 — 그 모호함을
    # 풀려고 하지 않고, 참여 자체를 거절해 애초에 그 상황이 생기지 않게 한다(설계 결정).
    # 이렇게 하면 아래 버리고 연결 분기는 "계정 하나 = 플레이어 전부"인 경우에만 돈다.
    joiner_accounts = await ctx.players.accounts_for(ctx.player_id)
    if len(joiner_accounts) > 1:
        return ToolResult(
            error="이미 다른 계정과 연결된 플레이어다. 먼저 /연결 해제로 풀고 다시 시도해 달라"
        )

    counts = await ctx.player_data.count(ctx.player_id) if ctx.player_data is not None else {}
    # 메모리(player_memory)는 코어 테이블이라 player_data 스캐너가 일부러 세지 않는다
    # (core/player_data.py) — 그래서 memory 서비스로 따로 본다. 안 보면 메모리만 있는 계정은
    # 경고 없이 연결되고, 그 메모리는 떨어져 나간 옛 플레이어에 영영 남는다.
    has_memory = bool(await ctx.memory.get(ctx.player_id))
    if counts or has_memory:
        view = _DiscardConfirmView(code=code, joiner_discord_id=joiner_discord_id, ctx=ctx)
        line_items = [f"- {table}: {n}건" for table, n in counts.items()]
        if has_memory:
            line_items.append("- 유니가 기억하는 내용(메모리): 있음")
        lines = "\n".join(line_items)
        return ToolResult(
            data={"counts": counts, "has_memory": has_memory},
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

        # 아직 아무것도 지우지 않는다 — "버리는 데 동의했다"는 표시는 아래 _proceed_to_mutual_confirm이
        # mark_joined와 같은 원자적 UPDATE 안에서 같이 남긴다(별도 UPDATE로 미리 써 두면, 같은
        # 유출된 코드로 다른 참여자가 경쟁할 때 그 표시가 실제로 이긴 참여자와 분리될 수 있다).
        # 실제로 지우는 일은 양쪽이 다 확인했을 때 한 번만 일어난다(_ConfirmButtonsView.confirm).
        result = await _proceed_to_mutual_confirm(
            self._ctx, link_code, self._joiner_discord_id, discard_agreed=True
        )
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
        if link_code.status in ("completed", "finalizing"):
            # 이미 끝났거나(두 번 클릭 등) 다른 클릭이 지금 막 마무리하고 있다 — 조용히
            # 안내만 한다. (이 검사는 사용자 경험을 위한 빠른 길일 뿐, 진짜 방어선은 아래
            # mark_finalizing의 원자적 UPDATE다 — 이 검사와 그 UPDATE 사이에도 다른 클릭이
            # 끼어들 수 있기 때문이다.)
            await interaction.response.send_message("이미 연결이 끝났다", ephemeral=True)
            return

        # 본인 확인은 "누른 계정이 속한 플레이어"로 한다 — 설계서: "확인 버튼은 연결된 계정이면
        # 어느 쪽에서 눌러도 된다". 발급자 쪽 DM은 notify.dm이 대표 계정으로 보내는데, /연결 시작을
        # 대표가 아닌 계정에서 쳤다면 DM을 받은 계정과 명령을 친 계정이 다르다 — 디스코드 ID
        # 하나와 비교하면 DM을 받은 쪽은 누를 수 없고 누를 수 있는 쪽은 DM을 못 받아 막힌다.
        presser = await self._ctx.players.player_for_discord(str(interaction.user.id))
        if self._side == "issuer":
            expected_player_id: str | None = link_code.issuer_player_id
        else:
            # 참여자는 join_link에서 계정 하나짜리 플레이어로 제한했으니 사실상 그 계정 하나뿐이다.
            joiner_player = (
                await self._ctx.players.player_for_discord(link_code.joiner_discord_id)
                if link_code.joiner_discord_id is not None
                else None
            )
            expected_player_id = joiner_player.id if joiner_player is not None else None
        if presser is None or expected_player_id is None or presser.id != expected_player_id:
            await interaction.response.send_message("이건 당신의 확인 요청이 아니다", ephemeral=True)
            return

        await models.set_confirmed(self._ctx.db, self._code, side=self._side)
        refreshed = await models.get_code(self._ctx.db, self._code)
        assert refreshed is not None

        if refreshed.issuer_confirmed and refreshed.joiner_confirmed:
            assert refreshed.joiner_discord_id is not None
            # 여기부터는 파괴적이다(데이터 삭제, 계정 재배치) — 그런데 여기 도달하기까지
            # set_confirmed → get_code 두 번의 await를 거쳤다. discord.py는 버튼 클릭마다
            # 별도의 asyncio 태스크를 돌리고, await 지점마다 다른 클릭이 끼어들 수 있다.
            # "상태를 읽고 나중에 행동하는" 방식(check-then-act)은 이 틈에서 안전하지 않다 —
            # 두 클릭이 동시에 여기 도달하면 link_account가 두 번 불려 UNIQUE 제약으로 터지거나,
            # 더 나쁘게는 첫 클릭이 이미 끝낸 뒤 두 번째 클릭이 "참여자였던 계정 = 지금은
            # 발급자 플레이어"를 다시 조회해 발급자의 데이터를 지워 버릴 수 있다(실제로
            # 재현됐다). mark_finalizing은 "내가 이 코드의 마무리를 가져간다"를 단일 SQL
            # UPDATE(WHERE status NOT IN ('finalizing','completed'))로 원자적으로 주장한다 —
            # 이 호출에서 실패(False)하면 다른 클릭이 이미 가져갔거나 끝냈다는 뜻이니 아무것도
            # 하지 않고 멈춘다.
            if not await models.mark_finalizing(self._ctx.db, self._code):
                await interaction.response.send_message("이미 처리되고 있다", ephemeral=True)
                return

            # 참여자 계정은 아직 자기 자신의(옛) 플레이어에 연결되어 있다 —
            # player_discord_accounts.discord_id는 유니크라 그대로 link_account를 부르면 깨진다.
            stale_joiner = await self._ctx.players.player_for_discord(refreshed.joiner_discord_id)
            if stale_joiner is not None:
                # 옛 플레이어가 이미 발급자 자신이면 뭔가 근본적으로 잘못됐다는 뜻이다(예: 마무리
                # 주장을 두 번 가져갈 수 있었다는 버그) — 조용히 발급자를 해체하거나 데이터를
                # 지우는 대신 여기서 터뜨린다.
                if stale_joiner.id == refreshed.issuer_player_id:
                    raise RuntimeError(
                        "account-link 불변 조건 위반: 참여자의 옛 플레이어가 이미 발급자와 같다"
                    )
                # join_link은 참여자 플레이어의 계정이 하나뿐일 때만 참여를 받는다(그래야 "이
                # 계정의 데이터"와 "이 플레이어의 데이터"가 같은 것이 된다). 하지만 확인 버튼은
                # 최대 CODE_TTL(10분)까지 떠 있고, 그 사이 참여자가 (완전히 다른, 동시에 진행
                # 중인) 별도의 연결 흐름으로 계정을 하나 더 얻었을 수 있다. 마무리 직전에 다시 센다.
                stale_accounts = await self._ctx.players.accounts_for(stale_joiner.id)
                single_account = len(stale_accounts) == 1
                if refreshed.discard_agreed:
                    # 버릴 데이터가 어느 계정 것인지 모호해졌다 — 지우지 않고 멈춘다. 여기서
                    # 멈추면 status는 'finalizing'에 남는다(이 드문 경우의 복구는 다루지 않는다).
                    if not single_account:
                        await interaction.response.send_message(
                            "참여자 계정 상태가 바뀌어 더 이상 안전하게 처리할 수 없다. "
                            "처음부터 다시 시도해 달라",
                            ephemeral=True,
                        )
                        return
                    if self._ctx.player_data is not None:
                        await self._ctx.player_data.delete(stale_joiner.id)
                    # 메모리는 코어 테이블이라 player_data가 지우지 않는다 — memory 서비스로 지운다.
                    await self._ctx.memory.clear(stale_joiner.id)
                if single_account:
                    # 옛 플레이어에는 지금 옮기는 이 계정 하나뿐이다 — 이 계정이 떠나면 아무도
                    # 닿을 수 없는 고아가 된다. unlink_account만 하면 그 고아가 태그(손으로 받은
                    # admin까지)를 영원히 쥐고 있어 players_with_tag("admin")이 유령 관리자를
                    # 세게 되고, admin의 "마지막 관리자는 못 뗀다" 검사가 속는다. dissolve_player는
                    # 계정 연결과 태그를 함께 지운다.
                    await self._ctx.players.dissolve_player(stale_joiner.id)
                else:
                    # (버릴 데이터가 없는 경로에서만 온다.) 옛 플레이어에 다른 계정이 남아 있으니
                    # 그 플레이어는 여전히 닿을 수 있다 — 이 계정 하나만 떼고 나머지 계정과 태그는
                    # 건드리지 않는다.
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
            # status는 여기서 건드리지 않는다 — issuer_confirmed/joiner_confirmed 두 불리언
            # 컬럼이 "누가 지금까지 확인했는가"를 이미 완전히 담고 있다. 예전에는 여기서
            # status를 "{side}_confirmed" 문자열로 덮어썼는데, 이 분기와 아래 완료 분기
            # 사이에도 다른 클릭이 끼어들 수 있어서(같은 코드에 두 쪽 다 확인된 뒤 이 느린
            # 쪽 쓰기가 나중에 끝나면) 이미 'completed'/'finalizing'으로 넘어간 status를
            # 되돌려 마무리를 한 번 더 돌게 만들 수 있었다(실제로 재현됐다). status의 전이는
            # pending → joined → finalizing → completed 뿐이고, 그 바깥 어떤 쓰기도 이
            # 분기에서는 하지 않는다.
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
    # 바꾸는 도구는 요청한 플레이어 본인의 데이터만 바꾼다(CLAUDE.md). unlink_account 자체는
    # 주인을 확인하지 않으므로, 남의 계정 ID를 넣어 남의 연결을 끊지 못하게 여기서 먼저 막는다.
    accounts = await ctx.players.accounts_for(ctx.player_id)
    if discord_id not in {a.discord_id for a in accounts}:
        return ToolResult(error="내 플레이어에 연결된 계정만 뗄 수 있다")

    player = await ctx.players.get_player(ctx.player_id)
    if player is None:
        return ToolResult(error="모르는 플레이어다")
    if player.primary_discord_id == discord_id and len(accounts) > 1:
        return ToolResult(error="대표 계정은 먼저 다른 계정으로 대표를 넘긴 뒤에 뗄 수 있다")

    await ctx.players.unlink_account(discord_id)
    await ctx.emit("players.linked", {"player_id": ctx.player_id, "discord_id": discord_id, "action": "unlinked"})
    return ToolResult(data={"unlinked": discord_id})
