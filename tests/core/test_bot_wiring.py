from pathlib import Path

import pytest

from core.bot import _migration_roots, build_client
from core.config import CoreConfig
from core.decorators import SkillCode
from core.manifest import SkillManifest
from core.registry import LoadedSkill


@pytest.mark.asyncio
async def test_build_client_registers_practice_commands(tmp_path: Path) -> None:
    config = CoreConfig(
        discord_token="fake-token",
        skills_dirs=[Path("tests/fixtures/skills_ok")],
        db_path=tmp_path / "test.db",
        test_guild_id=None,
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
