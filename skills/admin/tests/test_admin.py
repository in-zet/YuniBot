from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import cast

import aiosqlite
import pytest

from core.executor import ToolExecutor
from core.registry import SkillRegistry
from core.registry_snapshot import RegistrySnapshot, SkillSummary
from core.resolvers import ResolverRegistry
from core.tool_types import PlayerRef, ToolContext
from core.usage import UsageService, UsageSummary
from services.common.players import PlayersService
from services.common.settings import SettingsService


class _FakeUsageService:
    """admin.usage 테스트용 UsageService 더블 — 미리 정한 요약을 그대로 돌려준다.
    실제 UsageService를 상속하지 않아(aiosqlite 연결이 필요 없다) ToolContext.usage에 넣을 때는
    cast로 타입을 맞춘다(tests/core/test_slash.py의 _NO_DB와 같은 패턴)."""

    def __init__(self, summary: UsageSummary) -> None:
        self._summary = summary

    async def month_summary(self, *, guild_id: str, year_month: str) -> UsageSummary:
        return self._summary


_CORE_MIGRATIONS = [
    Path("migrations/core/001_players.sql").read_text(),
    Path("migrations/core/002_player_tags.sql").read_text(),
    Path("migrations/core/003_settings.sql").read_text(),
]
_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_admin_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def admin_env():
    conn = await aiosqlite.connect(":memory:")
    for migration in _CORE_MIGRATIONS:
        await conn.executescript(migration)
    await conn.commit()
    players = PlayersService(conn)
    settings = SettingsService(conn)
    skill = _load_skill()

    # admin은 registry_snapshot을 manifest.services에 선언했으므로 _dispatch가 채워 준다 —
    # 여기서도 같은 자리에 실제 모양의 스냅샷을 준다.
    snapshot = RegistrySnapshot(
        skills=tuple(
            SkillSummary(name=name, game="common", version=1, enabled=True)
            for name in ("admin", "account-link", "web-qa")
        ),
        failures={},
    )

    def make_ctx(player_id: str, *, guild_id: str | None = "g1", usage: UsageService | None = None) -> ToolContext:
        return ToolContext(
            player_id=player_id, tags=frozenset(), services={"players": players, "settings": settings},
            guild_id=guild_id, registry_snapshot=snapshot, usage=usage,
        )

    try:
        yield skill, players, settings, make_ctx
    finally:
        await conn.close()


async def test_grant_tag_rejects_status_tags(admin_env) -> None:
    skill, players, _, make_ctx = admin_env
    admin = await players.create_player()
    target = await players.create_player()
    await players.link_account(target.id, "222")

    result = await skill.grant_tag(make_ctx(admin.id), target=target, tag="chunithm-linked")

    assert result.error is not None
    assert "상태 태그" in result.error


async def test_grant_and_revoke_skill_tag(admin_env) -> None:
    skill, players, _, make_ctx = admin_env
    admin = await players.create_player()
    target = await players.create_player()

    granted = await skill.grant_tag(make_ctx(admin.id), target=target, tag="tester")
    assert granted.error is None
    assert await players.tags_for(target.id) == {"tester"}

    revoked = await skill.revoke_tag(make_ctx(admin.id), target=target, tag="tester")
    assert revoked.error is None
    assert await players.tags_for(target.id) == set()


async def test_revoke_tag_blocks_removing_the_last_admin(admin_env) -> None:
    skill, players, _, make_ctx = admin_env
    sole_admin = await players.create_player()
    await players.grant_tag(sole_admin.id, "admin", source="env_bootstrap")

    result = await skill.revoke_tag(make_ctx(sole_admin.id), target=sole_admin, tag="admin")

    assert result.error is not None
    assert "마지막" in result.error
    assert await players.tags_for(sole_admin.id) == {"admin"}


async def test_revoke_tag_allows_removing_admin_when_another_remains(admin_env) -> None:
    skill, players, _, make_ctx = admin_env
    admin1 = await players.create_player()
    admin2 = await players.create_player()
    await players.grant_tag(admin1.id, "admin", source="env_bootstrap")
    await players.grant_tag(admin2.id, "admin", source="admin:1")

    result = await skill.revoke_tag(make_ctx(admin1.id), target=admin2, tag="admin")

    assert result.error is None
    assert await players.tags_for(admin2.id) == set()


async def test_toggle_skill_sets_guild_scoped_flag(admin_env) -> None:
    skill, _, settings, make_ctx = admin_env
    ctx = make_ctx("admin1", guild_id="g1")

    result = await skill.toggle_skill(ctx, skill_name="web-qa", enabled=False)

    assert result.error is None
    assert await settings.is_skill_enabled(guild_id="g1", skill="web-qa") is False


@pytest.mark.parametrize("protected", ["admin", "account-link"])
async def test_toggle_skill_refuses_to_disable_protected_skills(admin_env, protected: str) -> None:
    """리뷰(최종) Fix 5: admin을 끄면 /관리 전체가 잠겨 되돌릴 길이 없다. account-link도 끌 수 없다."""
    skill, _, settings, make_ctx = admin_env

    result = await skill.toggle_skill(make_ctx("admin1", guild_id="g1"), skill_name=protected, enabled=False)

    assert result.error is not None
    assert "끌 수 없어" in result.error
    assert await settings.is_skill_enabled(guild_id="g1", skill=protected) is True


async def test_toggle_skill_rejects_unknown_skill_name(admin_env) -> None:
    skill, _, settings, make_ctx = admin_env

    result = await skill.toggle_skill(make_ctx("admin1", guild_id="g1"), skill_name="web-qq", enabled=False)

    assert result.error is not None
    assert "web-qq" in result.error
    # 오타 이름으로 설정 행이 생기지 않았다.
    cursor = await settings._conn.execute(  # pyright: ignore[reportPrivateUsage]
        "SELECT COUNT(*) FROM settings_skill_toggles"
    )
    row = await cursor.fetchone()
    assert row is not None and row[0] == 0


async def test_set_limits_stores_guild_scoped_values(admin_env) -> None:
    skill, _, settings, make_ctx = admin_env
    ctx = make_ctx("admin1", guild_id="g1")

    result = await skill.set_limits(ctx, monthly_cost_cap=25.0, player_cooldown_seconds=5)

    assert result.error is None
    assert await settings.get_value(
        scope="guild", scope_id="g1", skill="admin", name="월 비용 상한", default=0.0
    ) == 25.0
    assert await settings.get_value(
        scope="guild", scope_id="g1", skill="admin", name="플레이어 쿨다운", default=0
    ) == 5


async def test_set_report_channel_stores_bare_id(admin_env) -> None:
    skill, _, settings, make_ctx = admin_env
    ctx = make_ctx("admin1", guild_id="g1")

    result = await skill.set_report_channel(ctx, channel="123456789012345678")

    assert result.error is None
    assert await settings.get_value(
        scope="guild", scope_id="g1", skill="admin", name="보고 채널", default=""
    ) == "123456789012345678"


async def test_set_report_channel_parses_channel_mention(admin_env) -> None:
    skill, _, settings, make_ctx = admin_env
    ctx = make_ctx("admin1", guild_id="g1")

    result = await skill.set_report_channel(ctx, channel="<#123456789012345678>")

    assert result.error is None
    assert await settings.get_value(
        scope="guild", scope_id="g1", skill="admin", name="보고 채널", default=""
    ) == "123456789012345678"


async def test_set_report_channel_empty_string_turns_it_off(admin_env) -> None:
    skill, _, settings, make_ctx = admin_env
    ctx = make_ctx("admin1", guild_id="g1")
    await skill.set_report_channel(ctx, channel="123456789012345678")

    result = await skill.set_report_channel(ctx, channel="")

    assert result.error is None
    assert await settings.get_value(
        scope="guild", scope_id="g1", skill="admin", name="보고 채널", default="missing"
    ) == ""


async def test_set_report_channel_rejects_unreadable_value(admin_env) -> None:
    skill, _, _, make_ctx = admin_env
    ctx = make_ctx("admin1", guild_id="g1")

    result = await skill.set_report_channel(ctx, channel="그냥텍스트")

    assert result.error is not None


async def test_usage_returns_zero_when_no_calls_recorded(admin_env) -> None:
    skill, _, _, make_ctx = admin_env
    ctx = make_ctx("admin1", usage=cast(UsageService, cast(object, _FakeUsageService(UsageSummary(total_usd=0.0)))))

    result = await skill.usage(ctx)

    assert result.error is None
    assert result.data["calls"] == 0


async def test_usage_reports_real_totals_from_usage_service(admin_env) -> None:
    skill, _, _, make_ctx = admin_env
    summary = UsageSummary(
        total_usd=1.23, by_player={"p1": 1.23}, calls=4, total_input_tokens=100, total_output_tokens=20,
    )
    ctx = make_ctx("admin1", usage=cast(UsageService, cast(object, _FakeUsageService(summary))))

    result = await skill.usage(ctx)

    assert result.data["estimated_cost_usd"] == 1.23
    assert result.data["calls"] == 4


async def test_usage_requires_guild(admin_env) -> None:
    skill, _, _, make_ctx = admin_env
    ctx = make_ctx("admin1", guild_id=None)

    result = await skill.usage(ctx)

    assert result.error is not None


async def test_admin_skill_blocked_without_admin_tag() -> None:
    conn = await aiosqlite.connect(":memory:")
    for migration in _CORE_MIGRATIONS:
        await conn.executescript(migration)
    await conn.commit()
    players = PlayersService(conn)

    registry = SkillRegistry([Path("skills/admin")])
    registry.load()
    assert registry.failures() == {}

    resolvers = ResolverRegistry()
    resolvers.register(PlayerRef, players)
    executor = ToolExecutor(
        resolvers=resolvers, tag_provider=players, tag_definitions=registry.tag_definitions(),
        services={"players": players, "settings": SettingsService(conn)},
    )

    [(skill, spec)] = [(s, sp) for s, sp in registry.tool_specs() if sp.name == "usage"]
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(await players.tags_for(player.id)), services={})

    outcome = await executor.invoke(spec, ctx, {}, skill_tags=skill.manifest.tags, skill_name="admin")
    assert outcome.result is not None
    assert outcome.result.error == "모르는 명령이야"

    await players.grant_tag(player.id, "admin", source="test")
    ctx2 = ToolContext(
        player_id=player.id, tags=frozenset(await players.tags_for(player.id)), services={},
        guild_id="g1", usage=cast(UsageService, cast(object, _FakeUsageService(UsageSummary(total_usd=0.0)))),
    )
    outcome2 = await executor.invoke(spec, ctx2, {}, skill_tags=skill.manifest.tags, skill_name="admin")
    assert outcome2.result is not None
    assert outcome2.result.error is None

    await conn.close()
