# services/common/settings.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite


class SettingsService:
    """manifest.settings에 선언된 플레이어·서버 설정값과, 서버별 스킬 켜기/끄기를 저장한다."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get_value(
        self, *, scope: str, scope_id: str, skill: str, name: str, default: Any
    ) -> Any:
        cursor = await self._conn.execute(
            "SELECT value FROM settings_values WHERE scope = ? AND scope_id = ? AND skill = ? AND name = ?",
            (scope, scope_id, skill, name),
        )
        row = await cursor.fetchone()
        return default if row is None else json.loads(row[0])

    async def set_value(
        self, *, scope: str, scope_id: str, skill: str, name: str, value: Any, updated_by: str
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO settings_values (scope, scope_id, skill, name, value, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (scope, scope_id, skill, name) DO UPDATE SET "
            "value = excluded.value, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (scope, scope_id, skill, name, json.dumps(value), updated_by, now),
        )
        await self._conn.commit()

    async def scope_ids_with_value(self, *, scope: str, skill: str, name: str) -> list[tuple[str, Any]]:
        """그 설정을 명시적으로 넣은 scope_id 전부와 그 값. 기본값만 쓰는(한 번도 설정 안 한)
        scope_id는 저장된 행이 없어 포함되지 않는다 — 서버마다 도는 예약 작업이 "이 설정을
        켜 둔 서버가 어디냐"를 스스로 찾아야 할 때 쓴다(예: chunithm-updates의 알림 채널)."""
        cursor = await self._conn.execute(
            "SELECT scope_id, value FROM settings_values WHERE scope = ? AND skill = ? AND name = ?",
            (scope, skill, name),
        )
        rows = await cursor.fetchall()
        return [(scope_id, json.loads(value)) for scope_id, value in rows]

    async def is_skill_enabled(self, *, guild_id: str, skill: str) -> bool:
        cursor = await self._conn.execute(
            "SELECT enabled FROM settings_skill_toggles WHERE guild_id = ? AND skill = ?",
            (guild_id, skill),
        )
        row = await cursor.fetchone()
        return True if row is None else bool(row[0])

    async def set_skill_enabled(self, *, guild_id: str, skill: str, enabled: bool, updated_by: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO settings_skill_toggles (guild_id, skill, enabled, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (guild_id, skill) DO UPDATE SET enabled = excluded.enabled, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (guild_id, skill, 1 if enabled else 0, updated_by, now),
        )
        await self._conn.commit()
