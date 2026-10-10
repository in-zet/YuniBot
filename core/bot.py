# core/bot.py
from __future__ import annotations

import datetime
import logging
from pathlib import Path

import aiosqlite
import discord
from discord import app_commands

from core.config import CoreConfig, load_config
from core.contract_checks import run_all_contract_checks
from core.events import EventBus, handler_kind
from core.executor import ToolExecutor
from core.migrations import run_migrations
from core.player_data import PlayerDataScanner, discover_skill_tables
from core.registry import LoadedSkill, SkillRegistry
from core.registry_snapshot import build_snapshot
from core.resolvers import ResolverRegistry
from core.scheduler import Scheduler
from core.settings_slash import build_settings_group
from core.slash import attach_to_tree, build_slash_plan
from core.threads import ThreadManager
from core.tool_types import ArcadeRef, PlayerRef, ToolContext
from services.common.arcades import ArcadesService
from services.common.http import HttpService, build_http_service
from services.common.memory import MemoryService
from services.common.notify import NotifyService
from services.common.players import PlayersService
from services.common.secrets import SecretsService
from services.common.settings import SettingsService
from services.common.yuni import YuniService

logger = logging.getLogger(__name__)

CORE_MIGRATIONS_ROOT = Path("migrations/core")


class YuniClient(discord.Client):
    def __init__(
        self,
        *,
        intents: discord.Intents,
        test_guild_id: int | None,
        conn: aiosqlite.Connection,
        http_service: HttpService,
    ) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.conn = conn
        self.http_service = http_service
        self._test_guild_id = test_guild_id

    async def setup_hook(self) -> None:
        if self._test_guild_id:
            guild = discord.Object(id=self._test_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self) -> None:
        # http_service.aclose()는 hishel의 AsyncCacheTransport.aclose()를 거쳐 캐시용
        # SQLite 연결(anysqlite)까지 닫는다 — 코어 DB 연결을 닫는 아래 줄과 같은 역할이다.
        await self.http_service.aclose()
        await self.conn.close()
        await super().close()


class _BotDiscordGateway:
    """NotifyService가 쓰는 DiscordGateway 프로토콜의 유일한 구현. discord API는 여기서만 안다."""

    def __init__(self, discord_client: discord.Client) -> None:
        self._client = discord_client

    async def send_channel_message(self, channel_id: str, content: str) -> None:
        channel = self._client.get_channel(int(channel_id))
        # get_channel()의 반환형은 카테고리/포럼 채널도 포함한다 — 그런 채널에는 보낼 수
        # 없으므로(런타임에도 실패한다), Messageable로 좁혀진 것만 보낸다.
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(content)

    async def send_dm(self, discord_id: str, content: str, *, view: discord.ui.View | None = None) -> None:
        user = await self._client.fetch_user(int(discord_id))
        if view is not None:
            await user.send(content, view=view)
        else:
            await user.send(content)


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

    current_accounts: set[str] = set()
    for discord_id in admin_discord_ids:
        player = await players.player_for_discord(discord_id)
        if player is not None:
            for account in await players.accounts_for(player.id):
                current_accounts.add(account.discord_id)

    for player_id in await players.players_with_tag("admin", source="env_bootstrap"):
        accounts = await players.accounts_for(player_id)
        if not any(a.discord_id in admin_discord_ids for a in accounts):
            await players.revoke_tag(player_id, "admin")


def _register_event_handlers(bus: EventBus, registry: SkillRegistry) -> None:
    """스킬별로 핸들러 등록은 전부-아니면-전무다.

    handler_kind()는 @on 핸들러의 반환 타입 힌트를 get_type_hints()로 해석하는데, 그 힌트가
    핸들러 자신의 모듈 전역에서 풀리지 않는 이름이면 NameError가 난다(core/events.py 참고,
    Task 2 리뷰에서 의도적으로 가드를 안 둔 지점). 이 예외를 여기서 잡지 않으면 build_client()
    전체가 죽어 "한 스킬의 결함이 다른 스킬을 막지 않는다"는 보장이 깨진다. 그래서 스킬 하나의
    핸들러 등록 전체를 한 try/except로 묶어, 실패하면 그 스킬 전체를 registry.disable()로
    비활성화하고 다음 스킬로 넘어간다 — 계약 검사 실패를 처리하는 build_client의 방식과 같다.
    """
    for skill in registry.enabled_skills():
        try:
            for handler in skill.code.event_handlers:
                kind = handler_kind(handler.fn)
                if kind == "gather":
                    bus.register_gather(handler.event_name, owner_skill=skill.manifest.name, fn=handler.fn)
                else:
                    bus.register_notify(handler.event_name, owner_skill=skill.manifest.name, fn=handler.fn)
        except Exception as exc:  # noqa: BLE001 - 한 스킬의 어떤 실패도 코어 전체를 죽이면 안 된다
            registry.disable(skill.manifest.name, f"이벤트 핸들러 등록 실패: {exc}")


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

    players = PlayersService(conn)
    await _sync_admin_bootstrap(players, config.admin_discord_ids)

    skill_tables = await discover_skill_tables(conn)
    player_data = PlayerDataScanner(conn=conn, table_names=skill_tables)

    settings_service = SettingsService(conn)
    memory_service = MemoryService(conn)
    arcades_service = ArcadesService(conn)
    secrets_service = SecretsService(config.cookie_encryption_key) if config.cookie_encryption_key else None

    resolvers = ResolverRegistry()
    resolvers.register(PlayerRef, players)
    resolvers.register(ArcadeRef, arcades_service)

    services: dict[str, object] = {
        "players": players,
        "settings": settings_service,
        "memory": memory_service,
        "arcades": arcades_service,
    }
    if secrets_service is not None:
        services["secrets"] = secrets_service

    executor = ToolExecutor(
        resolvers=resolvers,
        tag_provider=players,
        tag_definitions=registry.tag_definitions(),
        services=services,
        event_bus=event_bus,
        player_data=player_data,
        settings=settings_service,
    )

    # http_service는 client보다 먼저 만든다 — client 생성자가 shutdown용으로 쥐고 있어야
    # 하기 때문이다(아래 YuniClient.close() 참고). notify_service는 반대로 client가 있어야
    # (게이트웨이가 discord.Client를 감싸므로) client 다음에 만든다.
    http_service = await build_http_service(cache_db_path=config.db_path.parent / "http_cache.sqlite")

    intents = discord.Intents.default()
    intents.message_content = True
    client = YuniClient(
        intents=intents, test_guild_id=config.test_guild_id, conn=conn, http_service=http_service
    )

    notify_service = NotifyService(
        gateway=_BotDiscordGateway(client),
        players=players,
        settings=settings_service,
        threads=ThreadManager(),
    )
    yuni_service = YuniService()

    services["http"] = http_service
    services["notify"] = notify_service
    services["yuni"] = yuni_service

    # 기동 시 한 번: 스킬의 @schedule을 DB와 맞추고, 꺼져 있던 동안 밀린 "오늘" 몫만 돌린다.
    # 평상시 실시간 폴링 루프는 @schedule을 쓰는 스킬이 생기는 단계에서 더한다(지금은 없다).
    # services가 전부 채워진 뒤에 돌려야 한다 — 밀린 작업이 ctx.notify/http/yuni를 쓰면
    # ToolContext.__getattr__이 UndeclaredServiceError를 던져 build_client 전체가 죽는다.
    scheduler = Scheduler(conn, quiet_start=config.quiet_hours_start, quiet_end=config.quiet_hours_end)
    now = datetime.datetime.now()  # noqa: DTZ005 - cron/정지 시간대는 서버 로컬 벽시계 시간 기준이다(UTC 타임스탬프가 아니다)
    recurring_specs = [
        (skill.manifest.name, spec.fn.__name__, spec.cron, spec.catch_up)
        for skill in registry.enabled_skills()
        for spec in skill.code.schedules
    ]
    await scheduler.sync_recurring(recurring_specs, now=now)
    due_recurring = await scheduler.startup_catch_up(now=now)
    schedule_fns = {
        (skill.manifest.name, spec.fn.__name__): spec
        for skill in registry.enabled_skills()
        for spec in skill.code.schedules
    }
    for owner_skill, fn_name in due_recurring:
        spec = schedule_fns[(owner_skill, fn_name)]
        ctx = ToolContext(
            player_id="system", tags=frozenset(), services=services, event_bus=event_bus, player_data=player_data
        )
        await spec.fn(ctx)
        await scheduler.mark_run(owner_skill, fn_name, cron=spec.cron, ran_at=now)

    due_once = await scheduler.startup_catch_up_once(now=now)
    for once in due_once:
        await event_bus.emit_notify(once.event_name, once.payload)

    plan = build_slash_plan(registry.tool_specs())
    snapshot = build_snapshot(registry)
    attach_to_tree(client.tree, plan, executor=executor, db=conn, registry_snapshot=snapshot)
    client.tree.add_command(
        build_settings_group(registry=registry, settings=settings_service, players=players)
    )

    return client, registry, executor


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    client, _registry, _executor = await build_client(config)
    try:
        await client.start(config.discord_token)
    finally:
        if not client.is_closed():
            await client.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
