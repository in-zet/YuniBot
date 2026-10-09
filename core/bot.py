# core/bot.py
from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite
import discord
from discord import app_commands

from core.config import CoreConfig, load_config
from core.executor import ToolExecutor
from core.migrations import run_migrations
from core.permissions import InMemoryTagProvider, TagProvider
from core.registry import LoadedSkill, SkillRegistry
from core.resolvers import ResolverRegistry
from core.slash import attach_to_tree, build_slash_plan

logger = logging.getLogger(__name__)

CORE_MIGRATIONS_ROOT = Path("migrations/core")


class YuniClient(discord.Client):
    def __init__(self, *, intents: discord.Intents, test_guild_id: int | None) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.tag_provider: TagProvider  # build_client()이 실행기와 같은 인스턴스로 채운다
        self._test_guild_id = test_guild_id

    async def setup_hook(self) -> None:
        if self._test_guild_id:
            guild = discord.Object(id=self._test_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


def _migration_roots(skills: list[LoadedSkill]) -> list[tuple[str, Path]]:
    """run_migrations에 넘길 (owner, 디렉터리) 목록을 "코어 먼저, 스킬은 이름순"으로 만든다.

    core를 skill_roots와 한꺼번에 sorted()하지 않는다 — 스킬 이름이 알파벳순으로
    "core"보다 앞서는 경우(예: "account-link")에도 core가 항상 먼저 와야 하기 때문이다.
    skill_roots만 따로 정렬한 뒤 core를 그 앞에 붙이는 리스트 리터럴 구조 자체가 이
    보장을 "우연한 정렬 결과"가 아니라 "코드 구조"로 고정한다.
    """
    skill_roots = sorted((skill.manifest.name, skill.path / "migrations") for skill in skills)
    return [("core", CORE_MIGRATIONS_ROOT), *skill_roots]


async def build_client(config: CoreConfig) -> tuple[YuniClient, SkillRegistry]:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)

    registry = SkillRegistry(config.skills_dirs)
    registry.load()
    for skill_dir, reason in registry.failures().items():
        logger.warning("skill disabled: %s (%s)", skill_dir, reason)

    roots = _migration_roots(registry.enabled_skills())
    async with aiosqlite.connect(config.db_path) as conn:
        await run_migrations(conn, roots=roots)
    # 이 단계(2단계)에서는 마이그레이션 적용 말고 런타임에 DB를 쓰는 서비스가 없다.
    # 그래서 연결은 마이그레이션이 끝나는 대로 바로 닫는다 - 열어 둔 채로 돌려주면
    # aiosqlite의 백그라운드 스레드가 아무도 닫지 않는 채 계속 떠 있게 된다.

    resolvers = ResolverRegistry()
    executor = ToolExecutor(
        resolvers=resolvers,
        tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(),
    )

    intents = discord.Intents.default()
    intents.message_content = True
    client = YuniClient(intents=intents, test_guild_id=config.test_guild_id)
    client.tag_provider = executor.tag_provider  # 실행기와 같은 임시 태그 제공자를 공유한다

    plan = build_slash_plan(registry.tool_specs())
    attach_to_tree(client.tree, plan, executor=executor)

    return client, registry


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    client, _registry = await build_client(config)
    await client.start(config.discord_token)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
