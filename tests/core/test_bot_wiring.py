import datetime
from pathlib import Path

import aiosqlite
import discord
import pytest

from core.bot import (  # pyright: ignore[reportPrivateUsage]
    _migration_roots,
    build_client,
)
from core.config import CoreConfig
from core.decorators import SkillCode
from core.manifest import SkillManifest
from core.registry import LoadedSkill
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

    client, registry = await build_client(config)

    assert "practice" in {s.manifest.name for s in registry.enabled_skills()}
    assert client.tree.get_command("연습") is not None  # 그룹: "연습 보기" 등

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

    client, registry = await build_client(_config(tmp_path, skills_root))

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

    client, registry = await build_client(_config(tmp_path, skills_root))

    assert {s.manifest.name for s in registry.enabled_skills()} == {"practice"}
    assert set(registry.failures()) == {"bad-name"}
    assert client.tree.get_command("연습") is not None

    await client.close()


@pytest.mark.asyncio
async def test_contract_check_failure_blocks_migrations_and_disables_skill(tmp_path: Path) -> None:
    # 접두사 규칙을 어긴 스킬의 마이그레이션이 조용히 적용되면 안 된다(적용한 SQL 파일은
    # 고칠 수 없으므로 DB에 영구히 남는 실수가 된다).
    config = _config(tmp_path, Path("tests/fixtures/skills_bad/bad_table_prefix"))

    client, registry = await build_client(config)

    assert {s.manifest.name for s in registry.enabled_skills()} == {"practice"}
    [reason] = registry.failures().values()
    assert "접두사" in reason

    async with aiosqlite.connect(config.db_path) as conn:
        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        tables = {row[0] for row in await cursor.fetchall()}
    assert "practice_demo_counter" in tables
    assert "totally_unrelated_table" not in tables

    await client.close()
