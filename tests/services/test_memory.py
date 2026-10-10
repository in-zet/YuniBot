# tests/services/test_memory.py
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from services.common.memory import MemoryService

_MIGRATION = Path("migrations/core/004_memory.sql").read_text()


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_get_defaults_to_empty_string(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    assert await service.get("p1") == ""


async def test_set_then_get_round_trips(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "호칭: 디짐상. 왼손이 약하다.")
    assert await service.get("p1") == "호칭: 디짐상. 왼손이 약하다."


async def test_set_rejects_content_over_1500_chars(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    with pytest.raises(ValueError, match="1500"):
        await service.set("p1", "가" * 1501)


async def test_clear_empties_content(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "내용")
    await service.clear("p1")
    assert await service.get("p1") == ""


async def test_auto_save_defaults_to_true_and_can_be_toggled(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    assert await service.get_auto_save("p1") is True

    await service.set_auto_save("p1", False)
    assert await service.get_auto_save("p1") is False


async def test_memory_is_scoped_per_player(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "p1의 메모리")
    assert await service.get("p2") == ""
