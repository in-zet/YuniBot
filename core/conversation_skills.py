# core/conversation_skills.py
from __future__ import annotations

import json

import aiosqlite

# 유니의 메시지에 달린 답장이 이 기간을 넘기면 펼친 스킬을 이어받지 않는다.
RETENTION_SECONDS = 7 * 24 * 3600


class ConversationSkillStore:
    """유니가 보낸 메시지 id → 그 답을 만들 때 펼쳐져 있던 스킬(오래 안 쓴 순서)."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, message_id: str, skills: list[str], *, now: float) -> None:
        if not skills:
            return
        _ = await self._db.execute(
            "INSERT INTO conversation_skills (message_id, skills, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(message_id) DO UPDATE SET skills = excluded.skills, created_at = excluded.created_at",
            (message_id, json.dumps(skills, ensure_ascii=False), now),
        )
        await self._db.commit()

    async def get(self, message_id: str) -> list[str]:
        cursor = await self._db.execute("SELECT skills FROM conversation_skills WHERE message_id = ?", (message_id,))
        row = await cursor.fetchone()
        if row is None:
            return []
        try:
            loaded = json.loads(row[0])
        except ValueError:
            return []
        return [s for s in loaded if isinstance(s, str)] if isinstance(loaded, list) else []

    async def purge(self, *, now: float) -> None:
        _ = await self._db.execute("DELETE FROM conversation_skills WHERE created_at < ?", (now - RETENTION_SECONDS,))
        await self._db.commit()
