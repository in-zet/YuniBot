from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import aiosqlite

from core.llm_pricing import estimate_cost_usd


@dataclass(slots=True, kw_only=True)
class UsageSummary:
    total_usd: float
    by_player: dict[str, float] = field(default_factory=dict)
    calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0


class UsageService:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def record(
        self,
        *,
        guild_id: str,
        player_id: str,
        model: str,
        kind: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> float:
        cost = estimate_cost_usd(
            model, input_tokens=input_tokens, output_tokens=output_tokens, cached_input_tokens=cached_input_tokens
        )
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO llm_usage (guild_id, player_id, model, kind, input_tokens, output_tokens, "
            "estimated_cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, player_id, model, kind, input_tokens, output_tokens, cost, now),
        )
        await self._conn.commit()
        return cost

    async def month_total_usd(self, *, guild_id: str, year_month: str) -> float:
        cursor = await self._conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM llm_usage "
            "WHERE guild_id = ? AND created_at LIKE ?",
            (guild_id, f"{year_month}%"),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0

    async def month_summary(self, *, guild_id: str, year_month: str) -> UsageSummary:
        cursor = await self._conn.execute(
            "SELECT player_id, estimated_cost_usd, input_tokens, output_tokens FROM llm_usage "
            "WHERE guild_id = ? AND created_at LIKE ?",
            (guild_id, f"{year_month}%"),
        )
        rows = await cursor.fetchall()
        summary = UsageSummary(total_usd=0.0)
        for player_id, cost, input_tokens, output_tokens in rows:
            summary.calls += 1
            summary.total_usd += cost
            summary.total_input_tokens += input_tokens
            summary.total_output_tokens += output_tokens
            summary.by_player[player_id] = summary.by_player.get(player_id, 0.0) + cost
        return summary
