# core/bot.py
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import aiosqlite
import discord
from discord import app_commands
from discord.ext import tasks

from core.address import AddressResolver, NicknameSource
from core.agent import AgentLoop
from core.config import CoreConfig, load_config
from core.contract_checks import run_all_contract_checks
from core.conversation_skills import ConversationSkillStore
from core.cooldown import CooldownTracker
from core.decorators import ScheduleSpec
from core.events import EventBus, handler_kind
from core.executor import ToolExecutor
from core.help_slash import build_help_command
from core.llm import LlmAdapter
from core.llm_claude import ClaudeLlmAdapter
from core.llm_gemini import GeminiLlmAdapter
from core.log_setup import configure_logging
from core.mention import ChannelMessage, MentionEntry, is_mention_eligible
from core.message_split import split_message
from core.migrations import run_migrations
from core.player_data import PlayerDataScanner, discover_skill_tables
from core.prompt import PromptAssembler
from core.registry import LoadedSkill, SkillRegistry
from core.registry_snapshot import build_snapshot
from core.resolvers import ResolverRegistry
from core.scheduler import OnceSchedule, Scheduler
from core.search import SearchAdapter
from core.search_claude import ClaudeSearchAdapter
from core.search_gemini import GeminiSearchAdapter
from core.settings_slash import build_settings_group
from core.slash import attach_to_tree, build_slash_plan
from core.tables import TableStore
from core.thread_memory import queue_thread_memory
from core.thread_view import ThreadEndView
from core.threads import ThreadManager, ThreadSession, ThreadTranscript
from core.tool_types import (
    ArcadeRef,
    ChartRef,
    DifficultyRef,
    Display,
    LampRef,
    PlayerRef,
    RankRef,
    ScoreRef,
    SongQuery,
    ToolContext,
)
from core.usage import UsageService
from services.chunithm.chart_ref import ChartRefResolver
from services.chunithm.chunithm_net import ChunithmNetService
from services.chunithm.records import RecordsService
from services.chunithm.songdb import SongDbService
from services.chunithm.terms import TermsService
from services.common.arcades import ArcadesService
from services.common.http import HttpService, build_http_service
from services.common.memory import SUMMARY_DELAY_SECONDS, MemoryService, PendingSummary
from services.common.notify import NotifyService
from services.common.players import PlayersService
from services.common.secrets import SecretsService
from services.common.settings import SettingsService
from services.common.websearch import WebsearchService
from services.common.yuni import YuniService

logger = logging.getLogger(__name__)

CORE_MIGRATIONS_ROOT = Path("migrations/core")


@dataclass(slots=True, kw_only=True)
class _RankRefAdapter:
    """ResolverRegistry는 타입 하나에 resolve(raw, ctx) 하나만 등록받는다 — TermsService는
    랭크/난이도/램프/점수를 이름이 다른 네 메서드로 노출하므로, 값 타입마다 이 얇은
    어댑터로 감싸 등록한다(TermsService 자신을 네 번 등록할 수 없다)."""

    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_rank(raw, ctx)


@dataclass(slots=True, kw_only=True)
class _DifficultyRefAdapter:
    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_difficulty(raw, ctx)


@dataclass(slots=True, kw_only=True)
class _LampRefAdapter:
    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_lamp(raw, ctx)


@dataclass(slots=True, kw_only=True)
class _ScoreRefAdapter:
    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_score(raw, ctx)


_SCHEDULE_POLL_SECONDS = 60
# 종료 직전 남은 메모리 요약을 마저 처리하는 데 기다리는 최대 시간(초). 못 끝낸 것은 DB에 남아 다음 기동에서 이어진다.
_SHUTDOWN_MEMORY_FLUSH_SECONDS = 20
# 자동 요약이 메모리를 바꿨을 때 그 대화의 마지막 메시지에 다는 반응.
_MEMORY_SAVED_EMOJI = "📝"


class YuniClient(discord.Client):
    def __init__(
        self,
        *,
        intents: discord.Intents,
        test_guild_id: int | None,
        conn: aiosqlite.Connection,
        http_service: HttpService,
        websearch_service: WebsearchService,
        mention_entry: MentionEntry,
        known_skill_names: set[str],
        skill_loading_mode: str,
        scheduler: Scheduler,
        schedule_fns: dict[tuple[str, str], ScheduleSpec],
        services: dict[str, object],
        event_bus: EventBus,
        player_data: PlayerDataScanner,
        memory_flush: Callable[[float], Awaitable[None]] | None = None,
        threads: ThreadManager | None = None,
        housekeeping: Callable[[float], Awaitable[None]] | None = None,
        tables: TableStore | None = None,
    ) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.conn = conn
        self.http_service = http_service
        self.websearch_service = websearch_service
        self._test_guild_id = test_guild_id
        self._mention_entry = mention_entry
        self._known_skill_names = known_skill_names
        self._skill_loading_mode = skill_loading_mode
        self._scheduler = scheduler
        self._schedule_fns = schedule_fns
        self._services = services
        self._event_bus = event_bus
        self._player_data = player_data
        # 시각이 된 메모리 자동 요약을 반영하는 콜러블(epoch 초를 받는다). 시험처럼 없으면 요약은 돌지 않는다.
        self._memory_flush = memory_flush
        self._threads = threads
        # 오래된 대화 상태 정리처럼 폴링 때마다 돌리는 잡일(epoch 초를 받는다).
        self._housekeeping = housekeeping
        # 24시간 지난 작업 표를 폴링 때마다 지운다.
        self._tables = tables

    async def setup_hook(self) -> None:
        assert self.user is not None  # setup_hook은 로그인 뒤에만 불린다(discord.py의 보장)
        self._mention_entry._bot_discord_id = str(self.user.id)
        if self._test_guild_id:
            guild = discord.Object(id=self._test_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self._poll_schedules.start()

    @tasks.loop(seconds=_SCHEDULE_POLL_SECONDS)
    async def _poll_schedules(self) -> None:
        """운영 중 돌면서 밀린 게 아니라 "지금 막 된" 예약을 돈다(기동 시 캐치업과는 다른
        경로 — core/scheduler.py의 poll_due_recurring/poll_due_once). 한 번의 폴링이 죽으면
        discord.py가 루프 자체를 멈추므로, 안에서 무슨 일이 나도 다음 틱은 돌아야 한다."""
        try:
            now = datetime.datetime.now()  # noqa: DTZ005 - cron/정지 시간대는 서버 로컬 벽시계 시간 기준이다
            due_recurring = await self._scheduler.poll_due_recurring(now=now)
            await _run_due_recurring(
                due_recurring, self._schedule_fns, scheduler=self._scheduler, services=self._services,
                event_bus=self._event_bus, player_data=self._player_data, db=self.conn, now=now,
            )
            due_once = await self._scheduler.poll_due_once(now=now)
            await _emit_due_once(due_once, self._event_bus)
        except Exception:
            logger.warning("예약 폴링 실패, 다음 틱에 다시 시도한다", exc_info=True)
        if self._threads is not None:
            # 무응답 대화 스레드에 "다 끝났나요?"를 묻고, 그 뒤로도 말이 없으면 닫는다. 닫힐 때 메모리 요약 대기 목록에 들어간다.
            await self._threads.check_idle(now=datetime.datetime.now(datetime.UTC))
        if self._housekeeping is not None:
            try:
                await self._housekeeping(datetime.datetime.now().timestamp())  # noqa: DTZ005 - 멘션 경로의 now.timestamp()와 같은 서버 로컬 시간 기준
            except Exception:
                logger.warning("대화 상태 정리 실패, 다음 틱에 다시 시도한다", exc_info=True)
        if self._tables is not None:
            self._tables.expire(time.time())
        if self._memory_flush is not None:
            try:
                await self._memory_flush(datetime.datetime.now().timestamp())  # noqa: DTZ005 - 멘션 경로의 now.timestamp()와 같은 서버 로컬 시간 기준
            except Exception:
                logger.warning("메모리 요약 폴링 실패, 다음 틱에 다시 시도한다", exc_info=True)

    async def _thread_for(self, message: discord.Message) -> ThreadSession | None:
        """이 메시지가 스킬이 연 대화 스레드(또는 DM) 안에서 그 대화의 플레이어가 쓴 것이면 그 대화를 돌려준다."""
        if self._threads is None or message.author.bot:
            return None
        session = self._threads.session_for_channel(str(message.channel.id))
        if session is None:
            return None
        players = cast("PlayersService", self._services["players"])
        author = await players.player_for_discord(str(message.author.id))
        return session if author is not None and author.id == session.player_id else None

    async def on_message(self, message: discord.Message) -> None:
        if message.author.id == (self.user.id if self.user else None):
            return
        trigger = ChannelMessage(
            message_id=str(message.id), author_discord_id=str(message.author.id), is_bot=message.author.bot,
            content=message.content, reference_id=str(message.reference.message_id) if message.reference else None,
            mentions_bot=self.user in message.mentions if self.user else False,
            channel_id=str(message.channel.id), author_display_name=message.author.display_name,
        )
        thread = await self._thread_for(message)
        if thread is None and not is_mention_eligible(
            trigger=trigger, bot_discord_id=str(self.user.id) if self.user else ""
        ):
            return
        if thread is not None and self._threads is not None:
            await self._threads.receive(
                thread.thread_id, thread.player_id, message.content,
                now=datetime.datetime.now(datetime.UTC), message_id=str(message.id),
            )

        history_messages = [
            msg async for msg in message.channel.history(limit=50, before=message)
        ]
        history = [
            ChannelMessage(
                message_id=str(m.id), author_discord_id=str(m.author.id), is_bot=m.author.bot, content=m.content,
                reference_id=str(m.reference.message_id) if m.reference else None,
                mentions_bot=self.user in m.mentions if self.user else False,
            )
            for m in reversed(history_messages)
        ] + [trigger]

        async def _send(text: str, displays: list[Display]) -> str | None:
            # 강제 표시물(숫자 규칙 위반 시 모은 전체 표시물, 또는 모델이 [[show:id]]로 직접
            # 고른 표시물)을 실제 디스코드 메시지에 붙인다. 멘션 경로는 인터랙션 응답이 아니라
            # 메시지 답장이라 ephemeral 개념이 없고(Display.ephemeral은 무시),
            # view는 이 경로로 올 일이 없어(confirm="always" 도구는 core/agent.py의
            # AgentLoop._invoke가 needs_confirm에서 이미 걸러 display 자체를 못 만든다) 스킵한다.
            embeds = [embed for display in displays for embed in display.embeds]
            extra_content = [display.content for display in displays if display.content is not None]
            full_text = "\n\n".join([text, *extra_content])
            # 2000자를 넘으면 디스코드가 통째로 거절해 답이 아예 안 나간다 — 줄 경계에서 나눠 첫 조각만
            # 답장으로, 나머지는 이어서 보낸다.
            chunks = split_message(full_text)
            sent = await message.reply(chunks[0], embeds=embeds, mention_author=False)
            for extra in chunks[1:]:
                await message.channel.send(extra)
            return str(sent.id)

        await self._mention_entry.handle(
            trigger=trigger, history=history,
            guild_id=str(message.guild.id) if message.guild else None,
            now=datetime.datetime.now(),  # noqa: DTZ005 - 쿨다운/정지 시간대와 같은 서버 로컬 시간 기준
            send=_send, typing=message.channel.typing(), thread=thread,
        )

    async def _wind_down(self) -> None:
        """꺼지기 직전: 열린 대화 스레드를 중단으로 닫아 전문을 넘기고, 대기 중인 메모리 요약을 마저 돌린다.
        요약이 늦으면 포기한다 — 대기 목록은 DB에 남아 다음 기동에서 이어진다."""
        if self._threads is not None:
            await self._threads.close_all(reason="shutdown")
        if self._memory_flush is None:
            return
        # 모든 대기 행이 '지금' 돌게 요약 대기 시간만큼 앞선 시각을 넘긴다. 실패한 행의 재시도 시각은 거기서 다시 밀린다.
        later = datetime.datetime.now().timestamp() + SUMMARY_DELAY_SECONDS  # noqa: DTZ005 - 멘션 경로의 now.timestamp()와 같은 서버 로컬 시간 기준
        try:
            await asyncio.wait_for(self._memory_flush(later), timeout=_SHUTDOWN_MEMORY_FLUSH_SECONDS)
        except Exception:
            logger.warning("종료 직전 메모리 요약을 끝내지 못했다, 다음 기동에서 이어간다", exc_info=True)

    async def close(self) -> None:
        # setup_hook에서 시작 안 했으면(테스트처럼 로그인 없이 build_client만 쓴 경우) 돌고 있지
        # 않으니 cancel()은 아무 일도 안 한다.
        self._poll_schedules.cancel()
        await self._wind_down()
        # http_service.aclose()는 hishel의 AsyncCacheTransport.aclose()를 거쳐 캐시용
        # SQLite 연결(anysqlite)까지 닫는다 — 코어 DB 연결을 닫는 아래 줄과 같은 역할이다.
        await self.http_service.aclose()
        await self.websearch_service.aclose()
        # LlmAdapter 프로토콜(core/llm.py)은 aclose()를 선언하지 않는다 — 테스트용
        # FakeLlmAdapter 등 다른 구현에는 없을 수 있는 메서드이기 때문이다. build_client가
        # 실제로 넣는 것은 항상 GeminiLlmAdapter 아니면 ClaudeLlmAdapter(LLM_PROVIDER에 따라)
        # 둘 중 하나이므로 여기서만 구체 타입으로 좁혀 닫는다.
        llm_adapter = cast("GeminiLlmAdapter | ClaudeLlmAdapter", self._mention_entry._agent._llm)
        await llm_adapter.aclose()
        await self.conn.close()
        await super().close()


class _BotDiscordGateway:
    """NotifyService가 쓰는 DiscordGateway 프로토콜의 유일한 구현. discord API는 여기서만 안다.

    client를 생성자에서 바로 쥐지 않고 get_client 콜러블로 늦게 묶는다. YuniClient.__init__이
    mention_entry를 요구해 client → mention_entry → notify_service → 이 게이트웨이 →
    client로 이어지는 순환 의존이 있기 때문이다. get_client는 실제로 메시지를 보낼 때
    (build_client()가 끝나고 client가 이미 만들어진 뒤)만 불리므로 안전하다."""

    def __init__(self, get_client: Callable[[], discord.Client]) -> None:
        self._get_client = get_client

    async def send_channel_message(
        self, channel_id: str, content: str, *, view: discord.ui.View | None = None
    ) -> None:
        client = self._get_client()
        # 캐시에 없는 DM 채널(재시작 뒤 등)은 fetch로 찾는다.
        channel = client.get_channel(int(channel_id)) or await client.fetch_channel(int(channel_id))
        # get_channel()의 반환형은 카테고리/포럼 채널도 포함한다 — 그런 채널에는 보낼 수
        # 없으므로(런타임에도 실패한다), Messageable로 좁혀진 것만 보낸다.
        if isinstance(channel, discord.abc.Messageable):
            if view is not None:
                await channel.send(content, view=view)
            else:
                await channel.send(content)

    async def open_dm_channel(self, discord_id: str) -> str:
        user = await self._get_client().fetch_user(int(discord_id))
        dm = await user.create_dm()
        return str(dm.id)

    async def send_dm(self, discord_id: str, content: str, *, view: discord.ui.View | None = None) -> None:
        user = await self._get_client().fetch_user(int(discord_id))
        if view is not None:
            await user.send(content, view=view)
        else:
            await user.send(content)

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        channel = self._get_client().get_channel(int(channel_id))
        if isinstance(channel, discord.abc.Messageable):
            message = await channel.fetch_message(int(message_id))
            await message.add_reaction(emoji)


def _migration_roots(skills: list[LoadedSkill]) -> list[tuple[str, Path]]:
    """run_migrations에 넘길 (owner, 디렉터리) 목록을 "코어 먼저, 스킬은 이름순"으로 만든다.

    core를 skill_roots와 한꺼번에 sorted()하지 않는다 — 스킬 이름이 알파벳순으로
    "core"보다 앞서는 경우(예: "account-link")에도 core가 항상 먼저 와야 하기 때문이다.
    skill_roots만 따로 정렬한 뒤 core를 그 앞에 붙이는 리스트 리터럴 구조 자체가 이
    보장을 "우연한 정렬 결과"가 아니라 "코드 구조"로 고정한다.
    """
    skill_roots = sorted((skill.manifest.name, skill.path / "migrations") for skill in skills)
    return [("core", CORE_MIGRATIONS_ROOT), *skill_roots]


async def _sync_admin_bootstrap(players: PlayersService, admin_discord_ids: frozenset[str]) -> None:
    """ADMIN_DISCORD_IDS에만 쓰인 admin 태그를 그 목록과 맞춘다.

    admin 스킬로 "추가로" 받은 admin 태그(source가 env_bootstrap이 아닌 것)는 건드리지
    않는다 — 그 사람들은 다른 경로로 admin이 됐으므로 .env 목록과 무관하게 유지된다.
    """
    for discord_id in admin_discord_ids:
        player = await players.player_for_discord(discord_id)
        if player is None:
            player = await players.create_player()
            await players.link_account(player.id, discord_id)
        await players.grant_tag(player.id, "admin", source="env_bootstrap")

    for player_id in await players.players_with_tag("admin", source="env_bootstrap"):
        accounts = await players.accounts_for(player_id)
        if any(a.discord_id in admin_discord_ids for a in accounts):
            continue
        all_admins = await players.players_with_tag("admin")
        if all_admins == [player_id]:
            # admin.md: "마지막 관리자의 admin 태그는 뗄 수 없다" — env 동기화도 예외가 아니다.
            logger.warning(
                "마지막 관리자(%s)가 ADMIN_DISCORD_IDS에서 빠졌지만, 관리자가 0명이 되는 것을 "
                "막기 위해 admin 태그를 유지한다", player_id,
            )
            continue
        await players.revoke_tag(player_id, "admin")


def _register_event_handlers(bus: EventBus, registry: SkillRegistry) -> None:
    """스킬별로 핸들러 등록은 전부-아니면-전무다.

    handler_kind()는 @on 핸들러의 반환 타입 힌트를 get_type_hints()로 해석하는데, 그 힌트가
    핸들러 자신의 모듈 전역에서 풀리지 않는 이름이면 NameError가 난다(core/events.py는
    의도적으로 가드를 두지 않는다). 이 예외를 여기서 잡지 않으면 build_client()
    전체가 죽어 "한 스킬의 결함이 다른 스킬을 막지 않는다"는 보장이 깨진다. 그래서 스킬 하나의
    핸들러 등록 전체를 한 try/except로 묶어, 실패하면 그 스킬 전체를 registry.disable()로
    비활성화하고 다음 스킬로 넘어간다 — 계약 검사 실패를 처리하는 build_client의 방식과 같다.
    """
    for skill in registry.enabled_skills():
        try:
            for handler in skill.code.event_handlers:
                kind = handler_kind(handler.fn)
                register = bus.register_gather if kind == "gather" else bus.register_notify
                register(
                    handler.event_name,
                    owner_skill=skill.manifest.name,
                    fn=handler.fn,
                    skill_services=list(skill.manifest.services),
                    tags=handler.tags,
                    tags_any=handler.tags_any,
                    # 스킬 manifest 레벨 조건도 넘긴다 — EventBus가 핸들러 조건과 따로 검사한다.
                    skill_tags=list(skill.manifest.tags),
                    skill_tags_any=list(skill.manifest.tags_any),
                )
        except Exception as exc:  # noqa: BLE001 - 한 스킬의 어떤 실패도 코어 전체를 죽이면 안 된다
            registry.disable(skill.manifest.name, f"이벤트 핸들러 등록 실패: {exc}")


async def _run_due_recurring(
    due: list[tuple[str, str]],
    schedule_fns: dict[tuple[str, str], ScheduleSpec],
    *,
    scheduler: Scheduler,
    services: dict[str, object],
    event_bus: EventBus,
    player_data: PlayerDataScanner,
    db: aiosqlite.Connection,
    now: datetime.datetime,
) -> None:
    """기동 시 밀린 반복 예약을 하나씩 돌린다. 한 작업이 실패해도 경고만 남기고 다음 작업으로
    넘어간다 — 그렇지 않으면 예약 하나의 결함이 build_client()를 터뜨려 봇 전체가 못 뜬다.
    실패한 작업은 mark_run하지 않는다(실행한 것이 아니므로)."""
    for owner_skill, fn_name in due:
        try:
            spec = schedule_fns[(owner_skill, fn_name)]
            ctx = ToolContext(
                player_id="system", tags=frozenset(), services=services, event_bus=event_bus,
                player_data=player_data, scheduler=scheduler, db=db,
            )
            await spec.fn(ctx)
            await scheduler.mark_run(owner_skill, fn_name, cron=spec.cron, ran_at=now)
        except Exception:  # 한 예약 작업의 어떤 실패도 기동 전체를 죽이면 안 된다
            logger.warning("밀린 예약 작업 '%s.%s' 실행 실패, 건너뛴다", owner_skill, fn_name, exc_info=True)


async def _emit_due_once(due_once: list[OnceSchedule], event_bus: EventBus) -> None:
    """기동 시 캐치업과 운영 중 폴링 루프가 똑같이 쓴다. OnceSchedule(core/scheduler.py)에는
    player_id/guild_id 필드가 없다 — payload에 실어 보낸 쪽(예: 목표 기한 알림)의 책임이므로,
    여기서는 방어적으로 꺼내고 없으면 "system"/길드 없음으로 떨어진다."""
    for once in due_once:
        player_id = str(once.payload.get("player_id", "system"))
        guild_id_raw = once.payload.get("guild_id")
        await event_bus.emit_notify(
            once.event_name,
            once.payload,
            player_id=player_id,
            guild_id=str(guild_id_raw) if guild_id_raw is not None else None,
        )


async def build_client(config: CoreConfig) -> tuple[YuniClient, SkillRegistry, ToolExecutor]:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)

    registry = SkillRegistry(config.skills_dirs)
    registry.load()
    # 계약 검사(테이블 접두사, 마이그레이션 파일 이름, docstring)를 마이그레이션보다 먼저 돌린다.
    # 적용한 SQL 파일은 고칠 수 없으므로, 규칙을 어긴 스킬의 마이그레이션이 한 번이라도 적용되면
    # DB에 영구히 남는 실수가 된다. 어긴 스킬은 통째로 비활성화한다(마이그레이션도, 명령도 없다).
    for skill_name, violations in run_all_contract_checks(registry).items():
        if violations:
            registry.disable(skill_name, "계약 검사 실패: " + "; ".join(violations))

    # 이벤트 핸들러 등록도 마이그레이션보다 먼저 끝내야 한다 — 여기서 비활성화되는 스킬이
    # 있으면 그 스킬의 마이그레이션도, 슬래시 명령도 함께 빠져야 하기 때문이다(아래
    # registry.enabled_skills() 호출들이 이 시점 이후의 _loaded를 본다).
    event_bus = EventBus()
    _register_event_handlers(event_bus, registry)

    for skill_dir, reason in registry.failures().items():
        logger.warning("skill disabled: %s (%s)", skill_dir, reason)

    roots = _migration_roots(registry.enabled_skills())
    conn = await aiosqlite.connect(config.db_path)
    await run_migrations(conn, roots=roots)

    # executor/event_bus보다 먼저 만든다 — 둘 다 생성 시점에 scheduler를 받아야 스킬이
    # ctx.scheduler로 예약을 걸거나 취소할 수 있다(아래).
    scheduler = Scheduler(conn, quiet_start=config.quiet_hours_start, quiet_end=config.quiet_hours_end)
    table_store = TableStore()

    players = PlayersService(conn)
    await _sync_admin_bootstrap(players, config.admin_discord_ids)

    skill_tables = await discover_skill_tables(conn)
    player_data = PlayerDataScanner(conn=conn, table_names=skill_tables)

    settings_service = SettingsService(conn)
    memory_service = MemoryService(conn)
    arcades_service = ArcadesService(conn)
    secrets_service = SecretsService(config.cookie_encryption_key) if config.cookie_encryption_key else None

    songdb_service = SongDbService(conn=conn)
    terms_service = TermsService(conn=conn)
    chunithm_net_service = (
        ChunithmNetService(conn=conn, secrets=secrets_service, players=players)
        if secrets_service is not None
        else None
    )
    records_service = RecordsService(conn=conn, songdb=songdb_service, players=players)
    chart_ref_resolver = ChartRefResolver(songdb=songdb_service, terms=terms_service)

    resolvers = ResolverRegistry()
    resolvers.register(PlayerRef, players)
    resolvers.register(ArcadeRef, arcades_service)
    resolvers.register(SongQuery, songdb_service)
    resolvers.register(ChartRef, chart_ref_resolver)
    resolvers.register(RankRef, _RankRefAdapter(terms=terms_service))
    resolvers.register(DifficultyRef, _DifficultyRefAdapter(terms=terms_service))
    resolvers.register(LampRef, _LampRefAdapter(terms=terms_service))
    resolvers.register(ScoreRef, _ScoreRefAdapter(terms=terms_service))

    services: dict[str, object] = {
        "players": players,
        "settings": settings_service,
        "memory": memory_service,
        "arcades": arcades_service,
        "songdb": songdb_service,
        "terms": terms_service,
        "records": records_service,
    }
    if secrets_service is not None:
        services["secrets"] = secrets_service
    if chunithm_net_service is not None:
        services["chunithm_net"] = chunithm_net_service

    executor = ToolExecutor(
        resolvers=resolvers,
        tag_provider=players,
        tag_definitions=registry.tag_definitions(),
        services=services,
        event_bus=event_bus,
        player_data=player_data,
        settings=settings_service,
        scheduler=scheduler,
        tables=table_store,
    )
    # services는 이 시점에 아직 notify/yuni/usage/http가 없지만, 같은 dict 객체를 그대로
    # 들고 있는다 — build_client()가 뒤에서 그 dict를 더 채우면(아래) EventBus도 같은
    # 참조를 보므로 최신 상태를 보게 된다(services 프로퍼티의 주석과 같은 이유).
    event_bus.bind_dispatch(
        services=services, tag_provider=players, settings=settings_service, db=conn, scheduler=scheduler,
        tables=table_store,
    )

    # http_service는 client보다 먼저 만든다 — client 생성자가 shutdown용으로 쥐고 있어야
    # 하기 때문이다(아래 YuniClient.close() 참고).
    http_service = await build_http_service(cache_db_path=config.db_path.parent / "http_cache.sqlite")
    # 켜진 직후: 국제판 수록 여부·신곡 플래그를 Chunimaru songdb로 한 번 덧씌운다.
    # refresh_from_chunimaru는 실패를 삼키고 로그만 남기므로, 네트워크가 안 되는
    # 기동에서도 build_client() 전체가 죽지 않는다.
    if config.fetch_chunimaru_on_startup:
        await songdb_service.refresh_from_chunimaru(http_service)

    # client → mention_entry → notify_service → _BotDiscordGateway → client로 이어지는
    # 순환을 끊으려고, notify_service의 게이트웨이는 client를 바로 쥐지 않고 아래
    # client_holder(런타임에만 채워지는 한 칸짜리 상자)를 통해 늦게 참조한다.
    client_holder: list[YuniClient] = []

    async def _on_thread_closed(transcript: ThreadTranscript) -> None:
        await queue_thread_memory(
            memory_service, transcript, now=datetime.datetime.now().timestamp()  # noqa: DTZ005 - 멘션 경로의 now.timestamp()와 같은 서버 로컬 시간 기준
        )

    thread_manager = ThreadManager(on_closed=_on_thread_closed)
    notify_service = NotifyService(
        gateway=_BotDiscordGateway(lambda: client_holder[0]),
        players=players,
        settings=settings_service,
        threads=thread_manager,
        end_button=lambda session: ThreadEndView(threads=thread_manager, players=players, session=session),
    )

    services["http"] = http_service
    services["notify"] = notify_service

    usage_service = UsageService(conn)
    services["usage"] = usage_service

    rules_text = Path("prompts/rules.md").read_text()
    persona_text = Path("prompts/persona.md").read_text()
    prompt_assembler = PromptAssembler(rules_text=rules_text, persona_text=persona_text, registry=registry)
    # 제공자(Gemini/Claude)는 부팅 시점 설정값이다(LLM_PROVIDER, 런타임 전환 없음).
    # google-genai의 Client(api_key="")는 "네트워크 없이 어댑터 객체만 만든다"가 아니라,
    # 생성 시점에 키가 비어 있으면 바로 ValueError를 던진다 — 테스트처럼 키 없이
    # build_client를 띄우는 경로를 막아서는 안 되므로(실제 호출은 멘션 경로에서 어차피
    # try/except로 LLM_FAILURE_MESSAGE로 감싼다), 빈 키는 더미 값으로 바꿔 구성만 통과시킨다.
    llm_adapter: LlmAdapter
    if config.llm_provider == "claude":
        if not config.anthropic_api_key:
            logger.warning(
                "ANTHROPIC_API_KEY가 비어 있다 - 설정 전까지 모든 멘션이 실패한다"
            )
        llm_adapter = ClaudeLlmAdapter(api_key=config.anthropic_api_key or "unset")
    else:
        if not config.gemini_api_key:
            logger.warning(
                "GEMINI_API_KEY가 비어 있다 - 설정 전까지 모든 멘션이 실패한다"
            )
        llm_adapter = GeminiLlmAdapter(
            api_key=config.gemini_api_key or "unset", fallback_model=config.fallback_model or None
        )

    # 호칭: 메모리의 호칭 칸 → CHUNITHM-NET 닉네임 → (멘션이면) 디스코드 서버 닉네임. 게임 쪽 닉네임은 소스로 받는다.
    address_sources: list[NicknameSource] = []
    if chunithm_net_service is not None:
        net_service = chunithm_net_service

        async def _net_nickname(player_id: str) -> str | None:
            return (await net_service.status(player_id)).nickname

        address_sources.append(_net_nickname)
    address_resolver = AddressResolver(memory=memory_service, sources=address_sources)

    # 유니의 한마디와 메모리 요약은 싼 모델(SUMMARY_MODEL)로 만든다. 규칙이 말투보다 앞선다(rules.md가 먼저).
    yuni_service = YuniService(
        llm=llm_adapter, model=config.summary_model, usage=usage_service, thinking_level=config.thinking_level,
        system_prompt=f"{rules_text}\n\n{persona_text}", address=address_resolver.resolve,
    )
    services["yuni"] = yuni_service

    # websearch_provider는 llm_provider와 독립이다 — 메인 에이전트와 검색이 다른 제공자를
    # 쓸 수 있다(00-core.md "websearch" 서비스, docs/plan.md §4 10단계 Part 2). 위 llm_adapter와
    # 같은 이유로 빈 키는 더미 값으로 바꿔 구성만 통과시킨다.
    search_adapter: SearchAdapter
    if config.websearch_provider == "claude":
        if not config.anthropic_api_key:
            logger.warning(
                "ANTHROPIC_API_KEY가 비어 있다 - 설정 전까지 모든 검색이 실패한다"
            )
        search_adapter = ClaudeSearchAdapter(api_key=config.anthropic_api_key or "unset")
    else:
        if not config.gemini_api_key:
            logger.warning(
                "GEMINI_API_KEY가 비어 있다 - 설정 전까지 모든 검색이 실패한다"
            )
        search_adapter = GeminiSearchAdapter(api_key=config.gemini_api_key or "unset")
    websearch_service = WebsearchService(
        adapter=search_adapter, model=config.search_model, cooldown_seconds=config.websearch_cooldown_seconds,
    )
    services["websearch"] = websearch_service

    # registry_snapshot은 슬래시 쪽(아래 attach_to_tree)보다 먼저 만든다 — AgentLoop도 같은
    # 스냅샷을 쥐고 있어야 멘션 경로의 admin_skills 같은 도구가 ctx.registry_snapshot을 쓸 수
    # 있다(core/slash.py의 _player_context와 같은 배선). registry는 이 시점 이후로
    # enabled_skills/failures가 바뀌지 않는다(계약 검사/이벤트 핸들러 등록에서 비활성화가 이미
    # 끝났다) — 여기서 한 번만 만들어 두 경로가 같은 스냅샷을 공유해도 안전하다.
    snapshot = build_snapshot(registry)
    agent_loop = AgentLoop(
        llm=llm_adapter, executor=executor, prompt=prompt_assembler,
        model=config.agent_model, thinking_level=config.thinking_level,
        max_tool_calls=config.max_tool_calls_per_turn, max_tokens=config.max_tokens_per_turn,
        db=conn, registry_snapshot=snapshot,
    )
    known_skill_names = {s.manifest.name for s in registry.enabled_skills()}
    conversation_skills = ConversationSkillStore(conn)
    mention_entry = MentionEntry(
        agent=agent_loop, players=players, notify=notify_service, cooldown=CooldownTracker(),
        settings=settings_service, usage=usage_service, bot_discord_id="",  # client.user가 생기기 전이라 setup_hook에서 채운다
        agent_model=config.agent_model, memory=memory_service,
        # executor와 같은 EventBus를 줘야 멘션으로 부른 도구가 낸 이벤트가 실제 등록된
        # 핸들러에 닿는다(M1) — executor도 이 event_bus 변수로 만들어졌으니 같은 객체다.
        event_bus=event_bus,
        known_skill_names=known_skill_names, skill_loading_mode=config.skill_loading_mode,
        address=address_resolver, conversation_skills=conversation_skills, threads=thread_manager,
    )

    async def _react_memory_saved(pending: PendingSummary) -> None:
        if pending.channel_id and pending.message_id:
            await notify_service.react(pending.channel_id, pending.message_id, _MEMORY_SAVED_EMOJI)

    async def housekeeping(now: float) -> None:
        await conversation_skills.purge(now=now)

    async def flush_memory(now: float) -> None:
        _ = await memory_service.flush_due(now=now, merge=yuni_service.merge_memory, on_changed=_react_memory_saved)

    schedule_fns = {
        (skill.manifest.name, spec.fn.__name__): spec
        for skill in registry.enabled_skills()
        for spec in skill.code.schedules
    }

    intents = discord.Intents.default()
    intents.message_content = True
    client = YuniClient(
        intents=intents, test_guild_id=config.test_guild_id, conn=conn, http_service=http_service,
        websearch_service=websearch_service,
        mention_entry=mention_entry, known_skill_names=known_skill_names,
        skill_loading_mode=config.skill_loading_mode,
        scheduler=scheduler, schedule_fns=schedule_fns, services=services, event_bus=event_bus,
        player_data=player_data, memory_flush=flush_memory, threads=thread_manager, housekeeping=housekeeping,
        tables=table_store,
    )
    client_holder.append(client)

    # 기동 시 한 번: 스킬의 @schedule을 DB와 맞추고, 꺼져 있던 동안 밀린 "오늘" 몫만 돌린다.
    # 평상시 실시간 폴링 루프(YuniClient._poll_schedules, setup_hook에서 시작)가 그 뒤를 잇는다.
    # services가 전부 채워진 뒤에 돌려야 한다 — 밀린 작업이 ctx.notify/http/yuni를 쓰면
    # ToolContext.__getattr__이 UndeclaredServiceError를 던져 build_client 전체가 죽는다.
    now = datetime.datetime.now()  # noqa: DTZ005 - cron/정지 시간대는 서버 로컬 벽시계 시간 기준이다(UTC 타임스탬프가 아니다)
    recurring_specs = [
        (skill.manifest.name, spec.fn.__name__, spec.cron, spec.catch_up)
        for skill in registry.enabled_skills()
        for spec in skill.code.schedules
    ]
    await scheduler.sync_recurring(recurring_specs, now=now)
    due_recurring = await scheduler.startup_catch_up(now=now)
    await _run_due_recurring(
        due_recurring, schedule_fns, scheduler=scheduler, services=services, event_bus=event_bus,
        player_data=player_data, db=conn, now=now,
    )

    due_once = await scheduler.startup_catch_up_once(now=now)
    await _emit_due_once(due_once, event_bus)

    plan = build_slash_plan(registry.tool_specs())
    attach_to_tree(client.tree, plan, executor=executor, db=conn, registry_snapshot=snapshot)
    client.tree.add_command(
        build_settings_group(registry=registry, settings=settings_service, players=players)
    )
    client.tree.add_command(build_help_command(registry=registry, players=players))

    return client, registry, executor


async def main() -> None:
    config = load_config()
    configure_logging(config.db_path.parent / "logs")
    client, _registry, _executor = await build_client(config)
    try:
        await client.start(config.discord_token)
    finally:
        if not client.is_closed():
            await client.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
