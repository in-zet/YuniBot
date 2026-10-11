import datetime
import logging
from pathlib import Path

import aiosqlite
import discord
import pytest
from cryptography.fernet import Fernet

from core.bot import (  # pyright: ignore[reportPrivateUsage]
    _migration_roots,
    _run_due_recurring,
    _sync_admin_bootstrap,
    build_client,
)
from core.config import CoreConfig
from core.decorators import ScheduleSpec, SkillCode
from core.manifest import SkillManifest
from core.registry import LoadedSkill
from core.tool_types import (
    ChartRef,
    DifficultyRef,
    LampRef,
    RankRef,
    ScoreRef,
    SongQuery,
)
from services.common.players import PlayersService
from tests.core.skill_factory import slash_tool, write_skill


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
    )

    client, registry, executor = await build_client(config)

    assert "practice" in {s.manifest.name for s in registry.enabled_skills()}
    assert client.tree.get_command("연습") is not None  # 그룹: "연습 보기" 등
    # 이 단계(18단계)에서 와이어링한 여섯 서비스가 전부 실행기까지 도달해야 한다 —
    # scheduler catch-up이 services 채우기보다 먼저 돌면 이 중 일부가 비어 있다.
    assert set(executor.services) == {
        "players", "settings", "memory", "arcades", "http", "notify", "yuni",
        "songdb", "terms", "records",
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

    async def ok(ctx: object) -> None:
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
                services={}, event_bus=EventBus(), player_data=PlayerDataScanner(conn=conn, table_names=()),
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
