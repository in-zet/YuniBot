import dataclasses
import datetime
import logging
from pathlib import Path
from typing import Any, cast

import aiosqlite
import discord
import pytest
from cryptography.fernet import Fernet

from core.bot import (  # pyright: ignore[reportPrivateUsage]
    _migration_roots,
    _register_event_handlers,
    _run_due_recurring,
    _sync_admin_bootstrap,
    build_client,
)
from core.config import CoreConfig
from core.decorators import EventHandlerSpec, ScheduleSpec, SkillCode
from core.events import EventBus
from core.fallback_messages import IDLE_CHECK_MESSAGES
from core.manifest import SkillManifest
from core.registry import LoadedSkill, SkillRegistry
from core.tables import TableBuilder, TableStore
from core.threads import ThreadTranscript
from core.tool_types import (
    ChartRef,
    DifficultyRef,
    LampRef,
    RankRef,
    ScoreRef,
    SongQuery,
    ToolContext,
)
from services.common.notify import NotifyService
from services.common.players import PlayersService
from services.common.websearch import WebsearchService
from tests.core.skill_factory import slash_tool, write_skill


class _FakeTagProvider:
    def __init__(self, tags: dict[str, set[str]] | None = None) -> None:
        self._tags = tags or {}

    async def tags_for(self, player_id: str) -> set[str]:
        return set(self._tags.get(player_id, set()))


@pytest.mark.asyncio
async def test_build_client_registers_practice_commands(tmp_path: Path) -> None:
    config = CoreConfig(
        discord_token="fake-token",
        skills_dirs=[Path("tests/fixtures/skills_ok")],
        db_path=tmp_path / "test.db",
        test_guild_id=None,
        admin_discord_ids=frozenset(),
        quiet_hours_start=datetime.time(2, 0),
        quiet_hours_end=datetime.time(6, 0),
        cookie_encryption_key=b"",
        fetch_chunimaru_on_startup=False,
    )

    client, registry, executor = await build_client(config)

    assert "practice" in {s.manifest.name for s in registry.enabled_skills()}
    assert client.tree.get_command("연습") is not None  # 그룹: "연습 보기" 등
    # build_client가 와이어링한 서비스가 전부 실행기까지 도달해야 한다 —
    # scheduler catch-up이 services 채우기보다 먼저 돌면 이 중 일부가 비어 있다.
    # usage는 admin.usage 등이 ctx.usage로 쓴다.
    assert set(executor.services) == {
        "players", "settings", "memory", "arcades", "http", "notify", "yuni",
        "songdb", "terms", "records", "usage", "websearch",
    }

    await client.close()


@pytest.mark.asyncio
async def test_build_client_registers_chunithm_services_and_resolvers(tmp_path: Path) -> None:
    config = CoreConfig(
        discord_token="fake-token",
        skills_dirs=[Path("tests/fixtures/skills_ok")],
        db_path=tmp_path / "test.db",
        test_guild_id=None,
        admin_discord_ids=frozenset(),
        quiet_hours_start=datetime.time(2, 0),
        quiet_hours_end=datetime.time(6, 0),
        cookie_encryption_key=Fernet.generate_key(),
        fetch_chunimaru_on_startup=False,
    )

    client, _registry, executor = await build_client(config)
    try:
        assert "songdb" in executor.services
        assert "terms" in executor.services
        assert "records" in executor.services
        assert "chunithm_net" in executor.services  # cookie 키가 있으므로 등록돼야 한다
        for ref_type in (SongQuery, ChartRef, RankRef, DifficultyRef, LampRef, ScoreRef):
            assert executor._resolvers.is_registered(ref_type)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_build_client_skips_chunimaru_fetch_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 테스트는 실제 사이트에 접속하지 않는다 — 스위치를 끄면 갱신 자체를 부르지 않아야 한다.
    from services.chunithm.songdb import SongDbService

    calls: list[object] = []

    async def _record(self: SongDbService, http: object) -> None:
        calls.append(http)

    monkeypatch.setattr(SongDbService, "refresh_from_chunimaru", _record)
    client, _registry, _executor = await build_client(_config(tmp_path))
    try:
        assert calls == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_register_event_handlers_scopes_ctx_to_skill_services_and_applies_tags() -> None:
    # _register_event_handlers가 skill.manifest.services와 핸들러의 tags/tags_any를
    # EventBus에 넘기는지 — 실제로 bind_dispatch 뒤 emit_notify를 해 보고, 핸들러가 받는
    # ctx.services가 선언한 서비스로만 좁혀지는지, 태그 조건이 적용되는지 끝에서 끝으로 확인한다.
    received: list[set[str]] = []

    async def handle(ctx: ToolContext, payload: dict[str, object]) -> None:
        received.append(set(ctx.services))

    code = SkillCode(
        event_handlers=[
            EventHandlerSpec(fn=handle, event_name="players.first_seen", tags=["chunithm-linked"])
        ]
    )
    skill = LoadedSkill(
        manifest=SkillManifest(
            name="notif-skill", description="테스트용", examples=["예시"], category="메모",
            game="common", version=1, services=["memory"],
        ),
        path=Path("."),
        code=code,
    )
    registry = SkillRegistry([])
    registry._loaded = {"notif-skill": skill}  # pyright: ignore[reportPrivateUsage]

    bus = EventBus()
    _register_event_handlers(bus, registry)
    bus.bind_dispatch(
        services={"memory": object(), "secrets": object()},
        tag_provider=_FakeTagProvider({"p1": {"chunithm-linked"}}),
        settings=None,
    )

    await bus.emit_notify("players.first_seen", {}, player_id="p1", guild_id=None)
    await bus.drain()

    assert received == [{"memory"}]  # secrets는 선언하지 않았으므로 ctx.services에 없다


@pytest.mark.asyncio
async def test_register_event_handlers_exposes_scheduler_only_when_declared() -> None:
    # scheduler는 db와 같은 ToolContext 전용 필드다(core/registry.py의 CTX_FIELD_SERVICE_NAMES) —
    # manifest.services에 적지 않은 스킬의 이벤트 핸들러는 ctx.scheduler로 예약을 걸 수 없어야 한다.
    seen: list[object] = []

    async def handle(ctx: ToolContext, payload: dict[str, object]) -> None:
        seen.append((ctx.scheduler, ctx.tables))

    code = SkillCode(event_handlers=[EventHandlerSpec(fn=handle, event_name="players.first_seen")])
    declared_skill = LoadedSkill(
        manifest=SkillManifest(
            name="declared-skill", description="테스트용", examples=["예시"], category="메모",
            game="common", version=1, services=["scheduler", "tables"],
        ),
        path=Path("."), code=code,
    )
    undeclared_skill = LoadedSkill(
        manifest=SkillManifest(
            name="undeclared-skill", description="테스트용", examples=["예시"], category="메모",
            game="common", version=1, services=[],
        ),
        path=Path("."), code=code,
    )
    registry = SkillRegistry([])
    registry._loaded = {"declared-skill": declared_skill, "undeclared-skill": undeclared_skill}  # pyright: ignore[reportPrivateUsage]

    fake_scheduler = object()
    fake_tables = TableStore()
    bus = EventBus()
    _register_event_handlers(bus, registry)
    bus.bind_dispatch(
        services={}, tag_provider=_FakeTagProvider({"p1": set()}), settings=None, scheduler=fake_scheduler,
        tables=fake_tables,
    )

    await bus.emit_notify("players.first_seen", {}, player_id="p1", guild_id=None)
    await bus.drain()

    # 두 핸들러의 실행 순서는 보장되지 않으니(둘 다 asyncio.create_task로 뜬다) 집합으로 본다.
    assert len(seen) == 2
    assert (fake_scheduler, fake_tables) in seen
    assert (None, None) in seen


@pytest.mark.asyncio
async def test_register_event_handlers_drops_handler_when_player_lacks_tag() -> None:
    received: list[str] = []

    async def handle(ctx: ToolContext, payload: dict[str, object]) -> None:
        received.append("called")

    code = SkillCode(
        event_handlers=[
            EventHandlerSpec(fn=handle, event_name="players.first_seen", tags=["chunithm-linked"])
        ]
    )
    skill = LoadedSkill(
        manifest=SkillManifest(
            name="notif-skill", description="테스트용", examples=["예시"], category="메모",
            game="common", version=1, services=[],
        ),
        path=Path("."),
        code=code,
    )
    registry = SkillRegistry([])
    registry._loaded = {"notif-skill": skill}  # pyright: ignore[reportPrivateUsage]

    bus = EventBus()
    _register_event_handlers(bus, registry)
    bus.bind_dispatch(services={}, tag_provider=_FakeTagProvider({"p1": set()}), settings=None)

    await bus.emit_notify("players.first_seen", {}, player_id="p1", guild_id=None)
    await bus.drain()

    assert received == []


@pytest.mark.asyncio
async def test_register_event_handlers_applies_skill_manifest_tags() -> None:
    # _register_event_handlers가 스킬 manifest의 tags도 EventBus에 넘기는지 — 핸들러 자신은 태그
    # 조건이 없어도, 스킬이 chunithm-synced를 요구하면 그 태그가 없는 플레이어에겐 핸들러가 안
    # 불린다(00-core.md: 이벤트 대상 플레이어가 그 스킬의 태그 조건을 갖췄는지).
    received: list[str] = []

    async def handle(ctx: ToolContext, payload: dict[str, object]) -> None:
        received.append(ctx.player_id)

    code = SkillCode(event_handlers=[EventHandlerSpec(fn=handle, event_name="players.first_seen")])
    skill = LoadedSkill(
        manifest=SkillManifest(
            name="notif-skill", description="테스트용", examples=["예시"], category="메모",
            game="common", version=1, services=[], tags=["chunithm-synced"],
        ),
        path=Path("."),
        code=code,
    )
    registry = SkillRegistry([])
    registry._loaded = {"notif-skill": skill}  # pyright: ignore[reportPrivateUsage]

    bus = EventBus()
    _register_event_handlers(bus, registry)
    bus.bind_dispatch(
        services={},
        tag_provider=_FakeTagProvider({"p1": set(), "p2": {"chunithm-synced"}}),
        settings=None,
    )

    await bus.emit_notify("players.first_seen", {}, player_id="p1", guild_id=None)
    await bus.emit_notify("players.first_seen", {}, player_id="p2", guild_id=None)
    await bus.drain()

    assert received == ["p2"]


@pytest.mark.asyncio
async def test_build_client_binds_event_bus_dispatch_to_live_services(tmp_path: Path) -> None:
    # build_client()가 event_bus.bind_dispatch(services=services, ...)를 실제로 불러야
    # @on 핸들러가 받는 ctx가 services 전체가 아니라 자기 manifest.services만 보게 된다.
    # executor.event_bus와 executor는 같은 services dict를 공유해야 한다(늦게 채워지는
    # notify/yuni/usage/http까지 EventBus가 최신으로 본다는 보장의 전제).
    config = _config(tmp_path)

    client, _registry, executor = await build_client(config)
    try:
        bound_services = executor.event_bus._services  # pyright: ignore[reportPrivateUsage]
        assert bound_services is executor.services
        assert executor.event_bus._tag_provider is not None  # pyright: ignore[reportPrivateUsage]
        assert executor.event_bus._settings is not None  # pyright: ignore[reportPrivateUsage]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_build_client_wires_scheduler_into_executor_and_event_bus(tmp_path: Path) -> None:
    # Scheduler를 executor/event_bus보다 먼저 만들어 둘에 넘겼는지 — 스킬이 ctx.scheduler로
    # 예약을 걸거나(슬래시/멘션 경로) 이벤트 핸들러 안에서 취소할 수 있으려면 둘 다 같은
    # Scheduler 인스턴스를 봐야 한다.
    config = _config(tmp_path)

    client, _registry, executor = await build_client(config)
    try:
        assert executor.scheduler is not None
        assert executor.event_bus._scheduler is executor.scheduler  # pyright: ignore[reportPrivateUsage]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_poll_schedules_runs_a_due_recurring_job_and_marks_it_run(tmp_path: Path) -> None:
    # 운영 중 폴링 루프(YuniClient._poll_schedules)가 실제로 돌아 @schedule 함수를 부르고
    # last_run_at을 남기는지 — 기동 시 캐치업과는 다른 경로(core/scheduler.py의
    # poll_due_recurring)를 끝에서 끝으로 확인한다.
    skills_root = tmp_path / "skills"
    body = (
        'from core.decorators import schedule\n\n\n'
        '@schedule("* * * * *")\n'
        "async def tick(ctx: ToolContext) -> None:\n"
        "    pass\n"
    )
    write_skill(skills_root, "ticker", body)

    client, _registry, _executor = await build_client(_config(tmp_path, skills_root))
    try:
        # sync_recurring이 기동 때 다음 분으로 잡아 뒀을 테니, 폴링이 반드시 집도록
        # next_run_at을 확실한 과거로 되돌린다.
        await client.conn.execute(
            "UPDATE schedule_recurring SET next_run_at = '2000-01-01T00:00:00' "
            "WHERE owner_skill = 'ticker' AND fn_name = 'tick'"
        )
        await client.conn.commit()

        await client._poll_schedules()  # pyright: ignore[reportPrivateUsage]

        cursor = await client.conn.execute(
            "SELECT last_run_at FROM schedule_recurring WHERE owner_skill = 'ticker' AND fn_name = 'tick'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] is not None  # mark_run이 불렸다 — 실행됐다는 뜻
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_poll_schedules_emits_a_due_once_schedule(tmp_path: Path) -> None:
    # 운영 중 폴링이 schedule_once도 집어서 이벤트로 낸다 — 목표 기한 알림(Part 3)이 바로
    # 이 경로를 쓴다. 여기서는 core/scheduler.py를 직접 써서 미리 하나 심어 둔다.
    config = _config(tmp_path)
    client, _registry, executor = await build_client(config)
    try:
        assert executor.scheduler is not None
        received: list[dict[str, object]] = []

        async def _capture(event_name: str, payload: dict[str, object], **_: object) -> None:
            received.append(payload)

        # emit_notify를 직접 가로채 due_once가 실제로 그 이벤트 이름/payload로 나가는지 본다
        # (받는 스킬이 없어도 emit은 일어난다 — 00-core.md "알림 이벤트").
        executor.event_bus.emit_notify = _capture  # pyright: ignore[reportAttributeAccessIssue]

        await executor.scheduler.schedule_once(
            owner_skill="ticker", event_name="test.tick.due",
            payload={"player_id": "p1", "goal_id": "g1"},
            fire_at=datetime.datetime(2000, 1, 1),  # noqa: DTZ001 - 예약은 서버 로컬 벽시계 기준, 확실한 과거
            catch_up=False,
        )

        await client._poll_schedules()  # pyright: ignore[reportPrivateUsage]

        assert received == [{"player_id": "p1", "goal_id": "g1"}]
    finally:
        await client.close()


def _fake_skill(tmp_path: Path, *, name: str) -> LoadedSkill:
    return LoadedSkill(
        manifest=SkillManifest(
            name=name,
            description="테스트용",
            examples=["예시"],
            category="메모",
            game="common",
            version=1,
        ),
        path=tmp_path,
        code=SkillCode(),
    )


def test_migration_roots_puts_core_first_even_when_a_skill_name_sorts_before_core(
    tmp_path: Path,
) -> None:
    # "account-link"는 알파벳 순으로 "core"보다 앞선다. core가 "항상 먼저"가 '우연히 코어가
    # 이름순으로도 앞이라서'가 아니라, skill_roots를 core와 따로 sorted()한 뒤 core를
    # 그 앞에 붙이는 구조 자체에서 나온다는 것을 고정한다.
    skills = [
        _fake_skill(tmp_path, name="zzz-last"),
        _fake_skill(tmp_path, name="account-link"),
    ]

    roots = _migration_roots(skills)

    assert [owner for owner, _ in roots] == ["core", "account-link", "zzz-last"]
    assert roots[0] == ("core", Path("migrations/core"))


def _config(tmp_path: Path, *skill_roots: Path) -> CoreConfig:
    return CoreConfig(
        discord_token="fake-token",
        skills_dirs=[Path("tests/fixtures/skills_ok"), *skill_roots],
        db_path=tmp_path / "test.db",
        test_guild_id=None,
        admin_discord_ids=frozenset(),
        quiet_hours_start=datetime.time(2, 0),
        quiet_hours_end=datetime.time(6, 0),
        cookie_encryption_key=b"",
        fetch_chunimaru_on_startup=False,
    )


_BROKEN_SKILLS: dict[str, str] = {
    # 한 스킬이 같은 이름을 단독 명령과 그룹으로 동시에 쓴다
    "self-group": slash_tool("a", "foo") + slash_tool("b", "foo bar"),
    # 한 스킬이 같은 경로를 두 번 쓴다
    "self-dup": slash_tool("a", "foo") + slash_tool("b", "foo"),
    # 4단계 경로
    "too-deep": slash_tool("a", "a b c d"),
    # 생성 콜백에 쓸 수 없는 인자 이름
    "bad-arg": slash_tool("a", "foo", args="__options: str"),
    # practice의 "연습 보기"(하위 명령)를 그룹으로 쓴다 — 루트 아래 단계 충돌
    "zz-collide": slash_tool("a", "연습 보기 더"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", sorted(_BROKEN_SKILLS))
async def test_one_broken_slash_skill_does_not_take_down_build_client(tmp_path: Path, broken: str) -> None:
    skills_root = tmp_path / "skills"
    _ = write_skill(skills_root, broken, _BROKEN_SKILLS[broken])

    client, registry, _executor = await build_client(_config(tmp_path, skills_root))

    assert {s.manifest.name for s in registry.enabled_skills()} == {"practice"}
    assert set(registry.failures()) == {broken}
    group = client.tree.get_command("연습")
    assert isinstance(group, discord.app_commands.Group)
    assert {c.name for c in group.commands} == {"체험시작", "보기", "비밀", "초기화"}

    await client.close()


@pytest.mark.asyncio
async def test_invalid_manifest_name_does_not_take_down_build_client(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    # 이 이름이 코어의 슬래시 콜백 코드 생성 단계까지 가면 SlashPlanError로 build_client 전체가 죽었다.
    _ = write_skill(skills_root, "bad-name", slash_tool("a", "foo"), manifest_name="x'); import os; ('")

    client, registry, _executor = await build_client(_config(tmp_path, skills_root))

    assert {s.manifest.name for s in registry.enabled_skills()} == {"practice"}
    assert set(registry.failures()) == {"bad-name"}
    assert client.tree.get_command("연습") is not None

    await client.close()


@pytest.mark.asyncio
async def test_contract_check_failure_blocks_migrations_and_disables_skill(tmp_path: Path) -> None:
    # 접두사 규칙을 어긴 스킬의 마이그레이션이 조용히 적용되면 안 된다(적용한 SQL 파일은
    # 고칠 수 없으므로 DB에 영구히 남는 실수가 된다).
    config = _config(tmp_path, Path("tests/fixtures/skills_bad/bad_table_prefix"))

    client, registry, _executor = await build_client(config)

    assert {s.manifest.name for s in registry.enabled_skills()} == {"practice"}
    [reason] = registry.failures().values()
    assert "접두사" in reason

    async with aiosqlite.connect(config.db_path) as conn:
        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        tables = {row[0] for row in await cursor.fetchall()}
    assert "practice_demo_counter" in tables
    assert "totally_unrelated_table" not in tables

    await client.close()


@pytest.mark.asyncio
async def test_unresolvable_event_handler_annotation_disables_only_that_skill(tmp_path: Path) -> None:
    # @on 핸들러의 반환 타입 힌트가 get_type_hints()로 풀리지 않으면(NameError) 그 스킬 하나만
    # 비활성화돼야 한다 — build_client() 전체가 죽어서는 안 된다(한 스킬의 결함이 다른 스킬의
    # 명령/마이그레이션을 막지 않는다는 보장은 이벤트 핸들러 등록에도 똑같이 적용된다).
    config = _config(tmp_path, Path("tests/fixtures/skills_bad/bad_event_handler_annotation"))

    client, registry, _executor = await build_client(config)

    assert {s.manifest.name for s in registry.enabled_skills()} == {"practice"}
    [reason] = registry.failures().values()
    assert "이벤트 핸들러" in reason
    assert "NotDefinedAnywhere" in reason  # NameError 메시지가 그대로 담겨 있어야 진단할 수 있다

    # 비활성화된 스킬의 명령도, practice의 명령도 정상이다 — 코어가 죽지 않았다는 증거.
    assert client.tree.get_command("연습") is not None

    await client.close()


async def test_failing_catch_up_job_is_isolated_and_others_still_run(caplog: pytest.LogCaptureFixture) -> None:
    """리뷰(최종) Fix 9: 밀린 예약 작업 하나가 터져도 build_client()가 죽지 않고, 다음 작업은 돈다."""
    import logging

    from core.events import EventBus
    from core.player_data import PlayerDataScanner

    ran: list[str] = []
    marked: list[tuple[str, str]] = []

    async def boom(ctx: object) -> None:
        raise RuntimeError("예약 작업 실패")

    async def ok(ctx: Any) -> None:
        # 예약 작업이 ctx.db를 쓸 수 있어야 한다(chunithm-updates가 db 없이 죽었던 적이 있다).
        assert ctx.db is not None
        ran.append("ok")

    class _FakeScheduler:
        async def mark_run(self, owner_skill: str, fn_name: str, *, cron: str, ran_at: datetime.datetime) -> None:
            marked.append((owner_skill, fn_name))

    conn = await aiosqlite.connect(":memory:")
    try:
        with caplog.at_level(logging.WARNING):
            await _run_due_recurring(
                [("a", "boom"), ("b", "ok")],
                {("a", "boom"): ScheduleSpec(fn=boom, cron="0 9 * * *"), ("b", "ok"): ScheduleSpec(fn=ok, cron="0 9 * * *")},
                scheduler=_FakeScheduler(),  # pyright: ignore[reportArgumentType]
                services={}, event_bus=EventBus(), player_data=PlayerDataScanner(conn=conn, table_names=()), db=conn,
                now=datetime.datetime(2026, 10, 10, 9, 0),  # noqa: DTZ001 - 예약은 서버 로컬 벽시계 기준(core/bot.py)
            )
    finally:
        await conn.close()

    assert ran == ["ok"]
    assert marked == [("b", "ok")]  # 실패한 작업은 돌린 것으로 기록하지 않는다
    assert "a.boom" in caplog.text


async def test_sync_admin_bootstrap_does_not_touch_manually_granted_admin() -> None:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(Path("migrations/core/001_players.sql").read_text())
    await conn.executescript(Path("migrations/core/002_player_tags.sql").read_text())
    await conn.commit()
    players = PlayersService(conn)

    env_admin = await players.create_player()
    await players.link_account(env_admin.id, "111")
    manual_admin = await players.create_player()
    await players.link_account(manual_admin.id, "222")
    await players.grant_tag(manual_admin.id, "admin", source="admin:111")

    await _sync_admin_bootstrap(players, frozenset({"111"}))
    assert await players.tags_for(env_admin.id) == {"admin"}
    assert await players.tags_for(manual_admin.id) == {"admin"}  # 수동 부여는 그대로

    # .env에서 111이 빠지면 env_bootstrap 소스만 회수된다.
    await _sync_admin_bootstrap(players, frozenset())
    assert await players.tags_for(env_admin.id) == set()
    assert await players.tags_for(manual_admin.id) == {"admin"}  # 여전히 안 건드린다

    await conn.close()


async def test_sync_admin_bootstrap_keeps_the_last_admin_even_if_dropped_from_env(
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(Path("migrations/core/001_players.sql").read_text())
    await conn.executescript(Path("migrations/core/002_player_tags.sql").read_text())
    await conn.commit()
    players = PlayersService(conn)

    only_admin = await players.create_player()
    await players.link_account(only_admin.id, "111")
    await _sync_admin_bootstrap(players, frozenset({"111"}))
    assert await players.tags_for(only_admin.id) == {"admin"}

    # 유일한 admin을 .env에서 뺐지만, 다른 admin이 없으므로 admin.md의
    # "마지막 관리자는 뗄 수 없다"가 env 동기화에도 그대로 적용돼야 한다.
    with caplog.at_level(logging.WARNING):
        await _sync_admin_bootstrap(players, frozenset())
    assert await players.tags_for(only_admin.id) == {"admin"}
    assert "마지막 관리자" in caplog.text

    await conn.close()


@pytest.mark.asyncio
async def test_build_client_registers_usage_service(tmp_path: Path) -> None:
    # gemini_api_key가 비어 있어도 build_client는 떠야 한다(실제 LLM 호출 없이 어댑터
    # 객체만 만들기 때문이다 — genai.Client(api_key="")는 생성 시점에 네트워크를 안 쓴다).
    config = _config(tmp_path)

    client, _registry, executor = await build_client(config)
    try:
        assert "usage" in executor.services
        assert client.tree.get_command("도움말") is not None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_build_client_wires_claude_adapter_when_provider_is_claude(tmp_path: Path) -> None:
    # anthropic_api_key가 비어 있어도 build_client는 떠야 한다(Gemini와 같은 이유 — 어댑터
    # 생성은 네트워크를 안 쓴다). llm_provider="claude"면 ClaudeLlmAdapter가 들어가야 한다.
    config = _config(tmp_path)
    config = dataclasses.replace(config, llm_provider="claude")

    client, _registry, _executor = await build_client(config)
    try:
        llm_adapter = client._mention_entry._agent._llm  # pyright: ignore[reportPrivateUsage]
        assert type(llm_adapter).__name__ == "ClaudeLlmAdapter"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_build_client_wires_gemini_search_adapter_by_default(tmp_path: Path) -> None:
    # websearch_provider는 llm_provider와 독립이다 — 기본값(gemini)이면 GeminiSearchAdapter가
    # services["websearch"] 안에 들어가야 한다.
    config = _config(tmp_path)

    client, _registry, executor = await build_client(config)
    try:
        websearch = cast("WebsearchService", executor.services["websearch"])
        search_adapter = websearch._adapter  # pyright: ignore[reportPrivateUsage]
        assert type(search_adapter).__name__ == "GeminiSearchAdapter"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_build_client_wires_claude_search_adapter_when_websearch_provider_is_claude(
    tmp_path: Path,
) -> None:
    config = dataclasses.replace(_config(tmp_path), websearch_provider="claude")

    client, _registry, executor = await build_client(config)
    try:
        websearch = cast("WebsearchService", executor.services["websearch"])
        search_adapter = websearch._adapter  # pyright: ignore[reportPrivateUsage]
        assert type(search_adapter).__name__ == "ClaudeSearchAdapter"
    finally:
        await client.close()


class _Transport:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, text: str) -> None:
        self.sent.append(text)


async def _open_test_thread(client, *, player_id: str, closed: list[ThreadTranscript], **kwargs):
    notify = client._services["notify"]  # pyright: ignore[reportPrivateUsage]
    transport = _Transport()
    session = await notify.open_thread(
        skill_name="practice", player_id=player_id, transport=transport, channel_id="thread-1",
        on_close=closed.append, now=datetime.datetime.now(datetime.UTC), **kwargs,
    )
    return session, transport


@pytest.mark.asyncio
async def test_poll_schedules_closes_an_idle_thread_and_queues_its_transcript_for_the_memory_summary(
    tmp_path: Path,
) -> None:
    client, _registry, _executor = await build_client(_config(tmp_path))
    try:
        closed: list[ThreadTranscript] = []
        session, transport = await _open_test_thread(
            client, player_id="p1", closed=closed, idle_timeout=0.0, close_timeout=0.0
        )
        assert client._threads is not None  # pyright: ignore[reportPrivateUsage]
        await client._threads.receive(  # pyright: ignore[reportPrivateUsage]
            session.thread_id, "p1", "<@1> 크로노 연습할게", now=datetime.datetime.now(datetime.UTC), message_id="m1"
        )

        await client._poll_schedules()  # pyright: ignore[reportPrivateUsage]
        assert len(transport.sent) == 1 and transport.sent[0] in IDLE_CHECK_MESSAGES
        await client._poll_schedules()  # pyright: ignore[reportPrivateUsage]

        assert [t.closed_reason for t in closed] == ["timeout"]
        cursor = await client.conn.execute("SELECT messages, channel_id, message_id FROM player_memory_pending")
        row = await cursor.fetchone()
        assert row is not None
        assert "크로노 연습할게" in row[0] and "<@1>" not in row[0]
        assert (row[1], row[2]) == ("thread-1", "m1")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_poll_schedules_expires_old_work_tables(tmp_path: Path) -> None:
    client, _registry, executor = await build_client(_config(tmp_path))
    try:
        store = executor.tables
        assert store is not None
        table = TableBuilder().column("a").build()
        _ = store.put("p1", "c1", table, now=0)  # 1970년에 만든 표
        _ = store.put("p1", "c1", table)  # 지금 만든 표

        await client._poll_schedules()  # pyright: ignore[reportPrivateUsage]

        assert store.ids("p1", "c1") == ["t2"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_closing_a_thread_drops_the_work_tables_of_that_thread_channel(tmp_path: Path) -> None:
    client, _registry, executor = await build_client(_config(tmp_path))
    try:
        store = executor.tables
        assert store is not None
        table = TableBuilder().column("a").build()
        _ = store.put("p1", "thread-1", table, now=0)
        _ = store.put("p1", "c1", table, now=0)
        closed: list[ThreadTranscript] = []
        session, _transport = await _open_test_thread(client, player_id="p1", closed=closed)
        assert client._threads is not None  # pyright: ignore[reportPrivateUsage]

        await client._threads.close(session.thread_id, reason="explicit")  # pyright: ignore[reportPrivateUsage]

        assert store.ids("p1", "thread-1") == []
        assert store.ids("p1", "c1") == ["t1"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_closing_the_client_closes_open_threads_as_shutdown(tmp_path: Path) -> None:
    client, _registry, _executor = await build_client(_config(tmp_path))
    closed: list[ThreadTranscript] = []
    _ = await _open_test_thread(client, player_id="p1", closed=closed)

    await client.close()

    assert [t.closed_reason for t in closed] == ["shutdown"]


@pytest.mark.asyncio
async def test_thread_for_matches_only_the_threads_player_in_the_threads_channel(tmp_path: Path) -> None:
    from types import SimpleNamespace

    client, _registry, _executor = await build_client(_config(tmp_path))
    try:
        players = cast("PlayersService", client._services["players"])  # pyright: ignore[reportPrivateUsage]
        owner = await players.create_player()
        await players.link_account(owner.id, "111")
        other = await players.create_player()
        await players.link_account(other.id, "222")
        session, _ = await _open_test_thread(client, player_id=owner.id, closed=[])

        def message(*, author_id: int, channel_id: str, bot: bool = False):
            return SimpleNamespace(author=SimpleNamespace(id=author_id, bot=bot), channel=SimpleNamespace(id=channel_id))

        find = client._thread_for  # pyright: ignore[reportPrivateUsage]
        assert await find(message(author_id=111, channel_id="thread-1")) is session  # pyright: ignore[reportArgumentType]
        assert await find(message(author_id=222, channel_id="thread-1")) is None  # pyright: ignore[reportArgumentType]
        assert await find(message(author_id=111, channel_id="elsewhere")) is None  # pyright: ignore[reportArgumentType]
        assert await find(message(author_id=111, channel_id="thread-1", bot=True)) is None  # pyright: ignore[reportArgumentType]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_poll_schedules_purges_old_conversation_skill_rows(tmp_path: Path) -> None:
    client, _registry, _executor = await build_client(_config(tmp_path))
    try:
        await client.conn.execute(
            "INSERT INTO conversation_skills (message_id, skills, created_at) VALUES ('old', '[\"a\"]', 0)"
        )
        await client.conn.commit()

        await client._poll_schedules()  # pyright: ignore[reportPrivateUsage]

        cursor = await client.conn.execute("SELECT COUNT(*) FROM conversation_skills")
        row = await cursor.fetchone()
        assert row is not None and row[0] == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_yuni_bond_records_first_met_through_the_real_event_bus_and_adds_its_prompt_context(
    tmp_path: Path,
) -> None:
    from core.tool_types import ToolContext

    client, registry, executor = await build_client(_config(tmp_path, Path("skills")))
    try:
        assert "yuni-bond" in {s.manifest.name for s in registry.enabled_skills()}
        players = cast("PlayersService", client._services["players"])  # pyright: ignore[reportPrivateUsage]
        player = await players.create_player()

        await executor.event_bus.emit_notify(
            "players.first_seen", {"player_id": player.id}, player_id=player.id, guild_id=None
        )
        await executor.event_bus.drain()

        cursor = await client.conn.execute(
            "SELECT kind FROM yuni_bond_milestones WHERE player_id = ?", (player.id,)
        )
        assert [row[0] for row in await cursor.fetchall()] == ["first_met"]

        # 열린 약속이 있으면 멘션 에이전트가 프롬프트 맥락으로 읽는다.
        agent = client._mention_entry._agent  # pyright: ignore[reportPrivateUsage]
        await client.conn.execute(
            "INSERT INTO yuni_bond_promises (id, player_id, text, created_on) VALUES ('x', ?, '크로노 연습', ?)",
            (player.id, datetime.datetime.now().date().isoformat()),  # noqa: DTZ005 - 스킬의 오늘 날짜와 같은 서버 로컬 기준
        )
        await client.conn.commit()
        snippets = await agent._skill_prompt_snippets(  # pyright: ignore[reportPrivateUsage]
            ToolContext(player_id=player.id, tags=frozenset(), services={}, guild_id=None)
        )
        assert any("크로노 연습" in s for s in snippets)
    finally:
        await client.close()


class _RecordingGateway:
    """DM으로 나가는 글만 모으는 가짜 게이트웨이(디스코드 없이 후기 흐름을 끝까지 돌린다)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, object | None]] = []

    async def send_channel_message(self, channel_id: str, content: str, *, view: object | None = None) -> None:
        self.sent.append((channel_id, content, view))

    async def open_dm_channel(self, discord_id: str) -> str:
        return f"dm-{discord_id}"

    async def send_dm(self, discord_id: str, content: str, *, view: object | None = None) -> None:
        self.sent.append((f"dm-{discord_id}", content, view))

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        pass


@pytest.mark.asyncio
async def test_session_review_flow_runs_through_the_real_bus_notify_and_thread_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, registry, executor = await build_client(_config(tmp_path, Path("skills")))
    try:
        skill = next(s for s in registry.enabled_skills() if s.manifest.name == "chunithm-session")
        monkeypatch.setitem(skill.code.tools[0].fn.__globals__, "_ASK_DELAY_SECONDS", 0.0)
        gateway = _RecordingGateway()
        notify = cast("NotifyService", client._services["notify"])  # pyright: ignore[reportPrivateUsage]
        notify._gateway = gateway  # pyright: ignore[reportPrivateUsage]
        players = cast("PlayersService", client._services["players"])  # pyright: ignore[reportPrivateUsage]
        player = await players.create_player()
        await players.link_account(player.id, "111")
        # 실제 경로에서는 이 이벤트가 나갈 때 이미 최소 한 번 동기화를 마친 뒤다
        # (records.save_sync가 첫 동기화 때 이 태그를 붙인다) — chunithm-session이
        # chunithm-synced를 요구하므로 여기서도 그 전제를 맞춰 준다.
        await players.set_status_tag(player.id, "chunithm-synced", source="records:first_sync")
        await executor.event_bus.emit_notify(
            "chunithm.sync.reported",
            {
                "player_id": player.id, "has_new_plays": True, "rendered_text": "세션 요약",
                "sections_data": [], "yuni_line": "", "sessions": [{"id": "s1", "play_date": "2026-10-05", "estimated": False}],
            },
            player_id=player.id, guild_id=None,
        )
        await executor.event_bus.drain()

        assert [(c, v is not None) for c, _t, v in gateway.sent] == [("dm-111", True)]  # 첫 말에 끝내기 버튼이 붙는다
        session = client._threads.session_for_channel("dm-111")  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
        assert session is not None and session.skill_name == "chunithm-session"
        assert "세션 요약" in session.guidance

        await client._threads.receive(session.thread_id, player.id, "좋았어", now=datetime.datetime.now(datetime.UTC))  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
        await client._threads.close(session.thread_id, reason="explicit")  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
        await executor.event_bus.drain()

        cursor = await client.conn.execute("SELECT status FROM session_reviews WHERE session_id = 's1'")
        row = await cursor.fetchone()
        assert row is not None and row[0] == "완료"
        # 후기 대화도 닫힐 때 평소처럼 메모리 요약 대기 목록에 들어간다(유니의 말은 빠진다).
        cursor = await client.conn.execute("SELECT messages FROM player_memory_pending WHERE player_id = ?", (player.id,))
        pending = await cursor.fetchone()
        assert pending is not None and "좋았어" in pending[0] and "어땠어" not in pending[0]
    finally:
        await client.close()
