import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core.db import GuardedConnection, connect_guarded, transaction


@pytest.fixture
async def conn(tmp_path: Path) -> AsyncIterator[GuardedConnection]:
    connection = await connect_guarded(tmp_path / "t.db")
    await connection.execute("CREATE TABLE t (name TEXT)")
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def _names(conn: aiosqlite.Connection) -> list[str]:
    cursor = await conn.execute("SELECT name FROM t ORDER BY name")
    return [row[0] for row in await cursor.fetchall()]


async def test_other_tasks_commit_waits_and_does_not_commit_a_half_written_transaction(conn: GuardedConnection) -> None:
    """한 작업이 여러 문장을 쓰는 중에 다른 작업이 커밋해도 앞의 쓰기가 같이 커밋되지 않고,
    앞이 롤백해도 다른 작업의 쓰기는 지워지지 않는다."""

    async def writer_that_fails() -> None:
        async with conn.transaction():
            await conn.execute("INSERT INTO t VALUES ('a-half')")
            await asyncio.sleep(0.05)  # 이 사이에 다른 작업이 끼어들려고 한다
            await conn.rollback()

    async def other_writer() -> None:
        await asyncio.sleep(0.01)
        await conn.execute("INSERT INTO t VALUES ('b')")
        await conn.commit()

    await asyncio.gather(writer_that_fails(), other_writer())

    assert await _names(conn) == ["b"]


async def test_owner_task_can_run_many_statements_inside_its_transaction(conn: GuardedConnection) -> None:
    async with conn.transaction():
        await conn.execute("INSERT INTO t VALUES ('x')")
        await conn.execute("INSERT INTO t VALUES ('y')")
        assert await _names(conn) == ["x", "y"]
        await conn.commit()
    assert await _names(conn) == ["x", "y"]


async def test_transaction_helper_is_noop_for_plain_connection() -> None:
    plain = await aiosqlite.connect(":memory:")
    try:
        async with transaction(plain):
            await plain.execute("CREATE TABLE u (n INTEGER)")
    finally:
        await plain.close()
