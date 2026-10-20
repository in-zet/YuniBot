# core/mention.py
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from core.agent import AgentLoop
from core.conversation_skills import ConversationSkillStore
from core.cooldown import CooldownTracker, check_monthly_cap
from core.events import EventBus
from core.fallback_messages import (
    COOLDOWN_ACTIVE_MESSAGES,
    LLM_FAILURE_MESSAGES,
    MONTHLY_CAP_REACHED_MESSAGES,
    PRIVATE_DM_FAILED_MESSAGES,
    RETRY_WAIT_MESSAGES,
    TOOL_CALL_CAP_MESSAGES,
    WORKING_MESSAGES,
    pick,
)
from core.response_assembly import assemble_response
from core.skill_loading import Mode, SkillLoadState
from core.threads import ThreadEndRequest, ThreadManager, ThreadSession
from core.tool_types import Display, ToolContext
from services.common.memory import PendingMessage, strip_mentions
from services.common.notify import NotifyService
from services.common.players import DiscordAccount, Player

# DM(guild_id=None)의 쿨다운/사용량 기록에 guild_id 대신 쓰는 고정 스코프 값. 월 상한만
# 서버별 설정(/관리 상한)이라 DM에는 적용할 상한 자체가 없다 — 쿨다운/사용량 기록은
# guild/DM을 가리지 않고 걸어야 하므로 이 문자열로 "서버가 아닌 대화"를 표시한다.
_DM_SCOPE = "dm"

MAX_CONTEXT_MESSAGES = 10

logger = logging.getLogger(__name__)


class _PlayersLike(Protocol):
    """PlayersService가 구조적으로 맞추는 부분 집합. core/cooldown.py의 _UsageLike/_SettingsLike와
    같은 이유로 Protocol을 쓴다 — 테스트가 진짜 aiosqlite 연결 없는 가짜 구현을 넣을 수 있다."""

    async def player_for_discord(self, discord_id: str) -> Player | None: ...
    async def accounts_for(self, player_id: str) -> list[DiscordAccount]: ...
    async def tags_for(self, player_id: str) -> set[str]: ...
    async def create_player(self) -> Player: ...
    async def link_account(self, player_id: str, discord_id: str) -> None: ...


class _MemoryLike(Protocol):
    """MemoryService가 구조적으로 맞추는 부분 집합 — 플레이어 메모리를 읽어 프롬프트에 넣고, 이번 대화에서
    본인이 쓴 글을 자동 요약 대기 목록에 쌓는다(core.remember 도구가 쓰는 set은 ctx.memory를 통해
    직접 부른다)."""

    async def get(self, player_id: str) -> str: ...

    async def enqueue(
        self,
        player_id: str,
        *,
        messages: list[PendingMessage],
        guild_id: str | None,
        channel_id: str | None,
        message_id: str | None,
        now: float,
    ) -> bool: ...


class _AddressLike(Protocol):
    async def resolve(self, player_id: str, *, discord_name: str | None = None) -> str | None: ...


@dataclass(slots=True, kw_only=True, frozen=True)
class ChannelMessage:
    message_id: str
    author_discord_id: str
    is_bot: bool
    content: str
    reference_id: str | None = None
    mentions_bot: bool = False
    channel_id: str | None = None
    # 디스코드 서버 닉네임(서버 밖이면 표시 이름). 호칭의 3순위로 쓴다.
    author_display_name: str | None = None


def is_mention_eligible(*, trigger: ChannelMessage, bot_discord_id: str) -> bool:
    """00-core.md "멘션이 말하는 조건" 표의 두 줄만 판정한다(멘션/멘션 알림 켜진 답장).
    버튼·스레드·슬래시·예약은 core/bot.py의 다른 입구가 다룬다."""
    if trigger.is_bot or trigger.author_discord_id == bot_discord_id:
        return False
    return trigger.mentions_bot


def build_conversation_context(
    messages: list[ChannelMessage],
    *,
    trigger: ChannelMessage,
    player_discord_ids: set[str],
    max_messages: int = MAX_CONTEXT_MESSAGES,
) -> list[ChannelMessage]:
    """같은 채널의 최근 메시지 중 '유니와 이 플레이어가 주고받은' 것만 추린다.

    trigger가 답장이면(reference_id가 있으면) 그 사슬을 거슬러 올라가며, 사슬이 끊기면(참조한
    메시지가 없거나 봇/이 플레이어가 아닌 다른 사람의 메시지면) 거기서 멈춘다. 답장이 아니면
    최근 채널 기록에서 '봇이 보냈거나 이 플레이어가 보낸' 메시지만 최근 순으로 최대
    max_messages개 모은다(다른 사람의 대화는 맥락에 안 섞인다 — 00-core.md "대화 맥락":
    "멘션이 없는 메시지는 맥락으로도 모으지 않는다"와 같은 원칙을 제3자 메시지에도 적용한다).
    """
    by_id = {m.message_id: m for m in messages}

    if trigger.reference_id is not None:
        chain: list[ChannelMessage] = [trigger]
        current = by_id.get(trigger.reference_id)
        while current is not None and len(chain) < max_messages:
            is_relevant = current.is_bot or current.author_discord_id in player_discord_ids
            if not is_relevant:
                break
            chain.append(current)
            current = by_id.get(current.reference_id) if current.reference_id else None
        return list(reversed(chain))

    relevant = [
        m for m in messages
        if m.message_id != trigger.message_id and (m.is_bot or m.author_discord_id in player_discord_ids)
    ]
    relevant.append(trigger)
    return relevant[-max_messages:]


class MentionEntry:
    def __init__(
        self,
        *,
        agent: AgentLoop,
        players: _PlayersLike,
        notify: NotifyService,
        cooldown: CooldownTracker,
        settings,
        usage,
        bot_discord_id: str,
        agent_model: str,
        memory: _MemoryLike,
        event_bus: EventBus,
        known_skill_names: set[str],
        skill_loading_mode: str,
        address: _AddressLike | None = None,
        conversation_skills: ConversationSkillStore | None = None,
        threads: ThreadManager | None = None,
    ) -> None:
        self._agent = agent
        self._players = players
        self._notify = notify
        self._cooldown = cooldown
        self._settings = settings
        self._usage = usage
        self._bot_discord_id = bot_discord_id
        # usage.record에 쓸 모델 이름은 설정값을 그대로 받는다(CLAUDE.md: "모델 이름은
        # 설정값이다. 코드에 박지 않는다") — AgentLoop에 실제로 설정된 모델과 다른
        # 문자열을 기록하면 비용 추정이 틀어지고, 요금표에 없는 모델명이면 월상한 집행
        # 자체가 조용히 멈춘다.
        self._agent_model = agent_model
        self._memory = memory
        # 멘션으로 부른 도구가 낸 이벤트가 executor에 등록된 진짜 핸들러에 닿으려면
        # ToolContext.event_bus가 그 executor와 같은 객체여야 한다(M1) — 기본값(매번 새
        # EventBus())을 그대로 두면 이벤트가 조용히 사라진다.
        self._event_bus = event_bus
        self._known_skill_names = known_skill_names
        self._skill_loading_mode = skill_loading_mode
        self._address = address
        self._conversation_skills = conversation_skills
        self._threads = threads

    async def _carried_skills(self, trigger: ChannelMessage, context: list[ChannelMessage]) -> list[str]:
        """답장 사슬에서 가장 가까운 유니 메시지가 남긴 '펼쳐 둔 스킬'을 찾는다. 답장이 아니면 새 대화다."""
        if self._conversation_skills is None or trigger.reference_id is None:
            return []
        candidates = [trigger.reference_id, *(m.message_id for m in reversed(context) if m.is_bot)]
        try:
            for message_id in candidates:
                skills = await self._conversation_skills.get(message_id)
                if skills:
                    return skills
        except Exception:
            logger.warning("펼친 스킬 목록 조회 실패, 새로 시작한다", exc_info=True)
        return []

    async def handle(
        self,
        *,
        trigger: ChannelMessage,
        history: list[ChannelMessage],
        guild_id: str | None,
        now: datetime,
        send: Callable[[str, list[Display]], Awaitable[str | None]],
        typing: AbstractAsyncContextManager[None],
        thread: ThreadSession | None = None,
    ) -> None:
        """thread가 있으면 스킬이 연 대화 스레드 안의 메시지다 — 멘션 없이 응답하고 그 스레드의 스킬을 이어 쓴다.
        누구의 메시지인지는 호출하는 쪽(core/bot.py)이 이미 확인했다."""
        if thread is None:
            if not is_mention_eligible(trigger=trigger, bot_discord_id=self._bot_discord_id):
                return
        elif trigger.is_bot or trigger.author_discord_id == self._bot_discord_id:
            return

        player = await self._players.player_for_discord(trigger.author_discord_id)
        is_new_player = player is None
        if player is None:
            # 슬래시 경로(core/slash.py의 _player_context)와 같은 흐름 — 처음 보는
            # 디스코드 계정이면 플레이어를 바로 만들고 연결한다(C2: 예전에는 여기서 그냥
            # return해 첫 멘션이 무응답이었다).
            player = await self._players.create_player()
            await self._players.link_account(player.id, trigger.author_discord_id)
            # 슬래시 경로(core/slash.py의 두 _dispatch/_build_modal_command 호출부)와 같은
            # 모양으로 생명주기 이벤트를 낸다(C2 재검토) — 아직 구독자는 없지만, 슬래시와
            # 멘션 모두 같은 "새 플레이어" 이벤트를 내야 나중에 붙는 구독자가 경로를
            # 가리지 않고 받는다.
            await self._event_bus.emit_notify(
                "players.first_seen", {"player_id": player.id}, player_id=player.id, guild_id=guild_id
            )
        if thread is not None and player.id != thread.player_id:
            return

        # 쿨다운은 guild/DM을 가리지 않고 건다 — DM에는 서버 설정이 없으니 admin 스킬의
        # "플레이어 쿨다운" 기본값(10초)을 그대로 쓴다. 월 상한은 서버별 설정(/관리 상한)뿐이라
        # DM에는 적용할 상한이 없다 — guild_id가 있을 때만 검사한다.
        cooldown_scope = guild_id if guild_id is not None else _DM_SCOPE
        if guild_id is not None:
            cooldown_seconds = int(
                await self._settings.get_value(
                    scope="guild", scope_id=guild_id, skill="admin", name="플레이어 쿨다운", default=10
                )
            )
        else:
            cooldown_seconds = 10
        if not self._cooldown.check(
            guild_id=cooldown_scope, player_id=player.id, cooldown_seconds=cooldown_seconds, now=now
        ):
            await send(pick(COOLDOWN_ACTIVE_MESSAGES), [])
            return

        if guild_id is not None:
            cap_status = await check_monthly_cap(usage=self._usage, settings=self._settings, guild_id=guild_id, now=now)
            if cap_status.exceeded:
                await send(pick(MONTHLY_CAP_REACHED_MESSAGES), [])
                return

        accounts = await self._players.accounts_for(player.id)
        player_discord_ids = {a.discord_id for a in accounts}
        context = build_conversation_context(history, trigger=trigger, player_discord_ids=player_discord_ids)
        user_text = "\n".join(f"{m.author_discord_id}: {m.content}" for m in context)

        prompt_context_snippets: list[str] = []
        if is_new_player:
            prompt_context_snippets.append("이 플레이어는 유니를 처음 멘션했다.")
        if thread is not None and thread.guidance:
            prompt_context_snippets.append(thread.guidance)

        # 요청한 본인의 호칭과 메모리만 프롬프트에 넣는다(00-core.md "주입 범위"). 호칭은 코드가 정한
        # 최종 이름이고, 모델은 "상"을 직접 붙이지 않는다.
        if self._address is not None:
            address = await self._address.resolve(player.id, discord_name=trigger.author_display_name)
            if address is not None:
                prompt_context_snippets.append(f"이 플레이어를 부를 이름: {address}")
        memory_text = await self._memory.get(player.id)
        if memory_text:
            prompt_context_snippets.append(f"[참고: 유니가 이 플레이어에 대해 기억하고 있는 내용]\n{memory_text}")

        tags = await self._players.tags_for(player.id)
        ctx = ToolContext(
            player_id=player.id, tags=frozenset(tags), services={}, guild_id=guild_id, usage=self._usage,
            event_bus=self._event_bus,
        )
        # self._skill_loading_mode는 생성자에서 str로 받는다(core/config.py의 CoreConfig는
        # Literal["instructions", "full"]이지만, MentionEntry의 공개 인터페이스를 core/skill_loading에
        # 묶어 두지 않으려고 여기서만 좁혀 쓴다 — 잘못된 문자열은 SkillLoadState.level_for가
        # "instructions"가 아닌 모든 값을 "card"로 접어 안전하게 처리한다).
        carried = (
            [thread.skill_name, *thread.opened_skills] if thread is not None
            else await self._carried_skills(trigger, context)
        )
        skill_state = SkillLoadState(
            mode=cast("Mode", self._skill_loading_mode), known_skills=self._known_skill_names, opened=carried,
        )

        async def reply(text: str, displays: list[Display]) -> None:
            # 이 답장에 단 답장이 같은 스킬을 펼친 채 시작하도록, 유니가 보낸 메시지 id에 목록을 남긴다.
            sent_id = await send(text, displays)
            if thread is not None:
                thread.opened_skills = skill_state.ordered_skills()
                thread.record_yuni(text, at=now)
            elif sent_id is not None and self._conversation_skills is not None:
                try:
                    await self._conversation_skills.save(
                        sent_id, skill_state.ordered_skills(), now=now.timestamp()
                    )
                except Exception:
                    logger.warning("펼친 스킬 목록 저장 실패", exc_info=True)

        # RETRY_WAIT_MESSAGES(503 재시도)와 WORKING_MESSAGES(heavy 스킬)는 같은 "잠깐만
        # 기다려 달라" 취지라 이번 요청에서 둘 중 하나만, 한 번만 보낸다 — 실기 2026-10-18:
        # 시간 기다렸다가 보내는 방식(20초 타이머)은 도구 2개 이상 부르는 평범한 턴에도
        # 거의 매번 떴다. 이제 스킬이 heavy로 표시된 경우에만, 그 도구를 부르는 순간 바로
        # 보낸다 — light 스킬만 쓰는 턴은 아무리 오래 걸려도 이 안내를 보내지 않는다.
        notice_sent = False

        async def on_retry(attempt: int, delay: float) -> None:
            nonlocal notice_sent
            if notice_sent:
                return
            notice_sent = True
            await send(pick(RETRY_WAIT_MESSAGES), [])

        async def on_heavy_call() -> None:
            nonlocal notice_sent
            if notice_sent:
                return
            notice_sent = True
            await send(pick(WORKING_MESSAGES), [])

        thread_end = ThreadEndRequest() if thread is not None else None
        async with typing:
            try:
                result = await self._agent.run(
                    ctx=ctx, skill_state=skill_state, user_text=user_text,
                    prompt_context_snippets=prompt_context_snippets, on_retry=on_retry, on_heavy_call=on_heavy_call,
                    thread_end=thread_end,
                )
            except Exception:  # LLM/네트워크 어떤 실패든 디스코드 핸들러를 죽이지 않고 고정 문구로 돌린다
                logger.warning("플레이어 %s의 멘션 처리 실패, 고정 문구로 응답한다", player.id, exc_info=True)
                await send(pick(LLM_FAILURE_MESSAGES), [])
                return

        # 사용량 기록도 guild/DM을 가리지 않는다 — DM 멘션은 guild_id가 없어 쿨다운
        # 스코프와 같은 고정값을 쓴다.
        await self._usage.record(
            guild_id=cooldown_scope, player_id=player.id, model=self._agent_model, kind="agent",
            input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
        )

        # 스레드 대화는 닫힐 때 전문으로 한 번에 요약 대기 목록에 들어간다(core/threads.py).
        # 관리 도구를 쓴 턴은 메모리에 남기지 않는다.
        if thread is None and not result.used_admin_tool:
            await self._queue_memory_summary(
                player_id=player.id, guild_id=guild_id, trigger=trigger, context=context,
                player_discord_ids=player_discord_ids, now=now,
            )

        if result.hit_tool_call_cap and result.text is None:
            await reply(pick(TOOL_CALL_CAP_MESSAGES), [])
            return

        assembled = assemble_response(result)
        # private 표시물(본인에게만 보여야 하는 내용)은 채널 답장에 붙이지 않고 DM으로 보낸다.
        public_displays: list[Display] = []
        dm_failed = False
        for display in assembled.displays:
            if display.private:
                if display.content:
                    dm_failed = not await self._send_private(player.id, display.content) or dm_failed
            else:
                public_displays.append(display)
        text = assembled.text or pick(LLM_FAILURE_MESSAGES)
        await reply(f"{text}\n{pick(PRIVATE_DM_FAILED_MESSAGES)}" if dm_failed else text, public_displays)
        # 모델이 끝내자고 한 대화는 마무리 말을 보낸 뒤 닫는다(닫힘 훅이 전문을 저장한다).
        if thread is not None and thread_end is not None and thread_end.requested and self._threads is not None:
            await self._threads.close(thread.thread_id, reason="explicit")

    async def _send_private(self, player_id: str, content: str) -> bool:
        try:
            await self._notify.dm(player_id, content)
        except Exception:
            logger.warning("플레이어 %s에게 비공개 DM을 보내지 못했다", player_id, exc_info=True)
            return False
        return True

    async def _queue_memory_summary(
        self,
        *,
        player_id: str,
        guild_id: str | None,
        trigger: ChannelMessage,
        context: list[ChannelMessage],
        player_discord_ids: set[str],
        now: datetime,
    ) -> None:
        """이 대화에서 본인(연결된 계정 포함)이 쓴 글만 요약 대기 목록에 쌓는다. 유니와 남의 말은 넣지 않는다."""
        own = [
            PendingMessage(id=m.message_id, text=strip_mentions(m.content))
            for m in context
            if not m.is_bot and m.author_discord_id in player_discord_ids
        ]
        try:
            _ = await self._memory.enqueue(
                player_id, messages=own, guild_id=guild_id, channel_id=trigger.channel_id,
                message_id=trigger.message_id, now=now.timestamp(),
            )
        except Exception:
            logger.warning("플레이어 %s의 메모리 요약 대기 등록 실패", player_id, exc_info=True)
