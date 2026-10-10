from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import aiosqlite
import pytest

from core.executor import ToolExecutor
from core.registry import SkillRegistry
from core.resolvers import ResolverRegistry
from core.tool_types import PlayerRef, ToolContext
from services.common.players import PlayersService
from services.common.settings import SettingsService

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

    def make_ctx(player_id: str, *, guild_id: str | None = "g1") -> ToolContext:
        return ToolContext(
            player_id=player_id, tags=frozenset(), services={"players": players, "settings": settings},
            guild_id=guild_id,
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


async def test_usage_returns_empty_placeholder(admin_env) -> None:
    skill, _, _, make_ctx = admin_env
    result = await skill.usage(make_ctx("admin1"))

    assert result.error is None
    assert result.data["calls"] == 0


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
    assert outcome.result.error == "모르는 명령이에요"

    await players.grant_tag(player.id, "admin", source="test")
    ctx2 = ToolContext(player_id=player.id, tags=frozenset(await players.tags_for(player.id)), services={})
    outcome2 = await executor.invoke(spec, ctx2, {}, skill_tags=skill.manifest.tags, skill_name="admin")
    assert outcome2.result is not None
    assert outcome2.result.error is None

    await conn.close()
