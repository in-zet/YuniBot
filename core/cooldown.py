# core/cooldown.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class _UsageLike(Protocol):
    async def month_total_usd(self, *, guild_id: str, year_month: str) -> float: ...


class _SettingsLike(Protocol):
    async def get_value(self, *, scope: str, scope_id: str, skill: str, name: str, default: object) -> object: ...


class CooldownTracker:
    """플레이어별 멘션 쿨다운. InMemoryTagProvider와 같은 이유로 메모리에만 둔다(재시작하면
    풀려도 10초 안팎의 쿨다운에서는 문제가 안 된다)."""

    def __init__(self) -> None:
        self._last_at: dict[tuple[str, str], datetime] = {}

    def check(self, *, guild_id: str, player_id: str, cooldown_seconds: int, now: datetime) -> bool:
        key = (guild_id, player_id)
        last = self._last_at.get(key)
        if last is not None and (now - last).total_seconds() < cooldown_seconds:
            return False
        self._last_at[key] = now
        return True


@dataclass(slots=True, kw_only=True, frozen=True)
class MonthlyCapStatus:
    cap_usd: float
    spent_usd: float
    exceeded: bool
    crossed_80_percent: bool


async def check_monthly_cap(
    *, usage: _UsageLike, settings: _SettingsLike, guild_id: str, now: datetime
) -> MonthlyCapStatus:
    cap = await settings.get_value(
        scope="guild", scope_id=guild_id, skill="admin", name="월 비용 상한", default=10.0
    )
    # core/usage.py는 기록 시각을 UTC로 저장한다(datetime.now(UTC)) — now는 서버 로컬
    # 벽시계(naive)라 그대로 strftime하면 UTC가 아닌 타임존에서 월 경계 앞뒤 몇 시간
    # 동안 "이번 달" 집계가 실제 저장된 달과 어긋난다. naive datetime의 astimezone()은
    # 시스템 로컬 시간으로 해석해 변환하므로 이걸로 맞춘다.
    year_month = now.astimezone(UTC).strftime("%Y-%m")
    spent = await usage.month_total_usd(guild_id=guild_id, year_month=year_month)
    cap_f = float(cap)  # pyright: ignore[reportArgumentType]
    return MonthlyCapStatus(
        cap_usd=cap_f, spent_usd=spent, exceeded=spent >= cap_f, crossed_80_percent=spent >= cap_f * 0.8,
    )
