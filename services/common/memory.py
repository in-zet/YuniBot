# services/common/memory.py
from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

MAX_LENGTH = 1500


class MemoryService:
    """플레이어 메모리 저장소. 자동 요약(12단계)과 memory 스킬(12단계)이 쓸 보기·지우기
    API까지만 만든다 — 요약을 만드는 로직과 프롬프트 주입은 여기 없다."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get(self, player_id: str) -> str:
        cursor = await self._conn.execute(
            "SELECT content FROM player_memory WHERE player_id = ?", (player_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else ""

    async def set(self, player_id: str, content: str) -> None:
        if len(content) > MAX_LENGTH:
            raise ValueError(f"메모리는 {MAX_LENGTH}자를 넘을 수 없다({len(content)}자)")
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO player_memory (player_id, content, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (player_id) DO UPDATE SET content = excluded.content, "
            "updated_at = excluded.updated_at",
            (player_id, content, now),
        )
        await self._conn.commit()

    async def clear(self, player_id: str) -> None:
        await self.set(player_id, "")

    async def get_auto_save(self, player_id: str) -> bool:
        cursor = await self._conn.execute(
            "SELECT auto_save FROM player_memory WHERE player_id = ?", (player_id,)
        )
        row = await cursor.fetchone()
        return True if row is None else bool(row[0])

    async def set_auto_save(self, player_id: str, enabled: bool) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO player_memory (player_id, auto_save, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (player_id) DO UPDATE SET auto_save = excluded.auto_save, "
            "updated_at = excluded.updated_at",
            (player_id, 1 if enabled else 0, now),
        )
        await self._conn.commit()
