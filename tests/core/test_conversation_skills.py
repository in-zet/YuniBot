# tests/core/test_conversation_skills.py
from pathlib import Path

import aiosqlite
import pytest

from core.conversation_skills import RETENTION_SECONDS, ConversationSkillStore
from core.migrations import run_migrations


@pytest.fixture
async def store(tmp_path: Path):
    conn = await aiosqlite.connect(tmp_path / "t.db")
    await run_migrations(conn, roots=[("core", Path("migrations/core"))])
    yield ConversationSkillStore(conn)
    await conn.close()


async def test_save_then_get_keeps_order(store: ConversationSkillStore) -> None:
    await store.save("m1", ["b", "a"], now=100.0)
    assert await store.get("m1") == ["b", "a"]


async def test_unknown_message_has_no_skills(store: ConversationSkillStore) -> None:
    assert await store.get("nope") == []


async def test_saving_nothing_leaves_no_row(store: ConversationSkillStore) -> None:
    await store.save("m1", [], now=100.0)
    assert await store.get("m1") == []


async def test_save_overwrites_the_same_message(store: ConversationSkillStore) -> None:
    await store.save("m1", ["a"], now=100.0)
    await store.save("m1", ["b"], now=200.0)
    assert await store.get("m1") == ["b"]


async def test_purge_drops_only_old_rows(store: ConversationSkillStore) -> None:
    await store.save("old", ["a"], now=0.0)
    await store.save("new", ["b"], now=RETENTION_SECONDS)
    await store.purge(now=RETENTION_SECONDS + 1)
    assert await store.get("old") == []
    assert await store.get("new") == ["b"]
