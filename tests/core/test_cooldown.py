# tests/core/test_cooldown.py
from datetime import UTC, datetime

import pytest

from core.cooldown import CooldownTracker, check_monthly_cap


def test_cooldown_tracker_blocks_within_window() -> None:
    tracker = CooldownTracker()
    now = datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC)
    assert tracker.check(guild_id="g1", player_id="p1", cooldown_seconds=10, now=now) is True
    assert tracker.check(guild_id="g1", player_id="p1", cooldown_seconds=10, now=now) is False


def test_cooldown_tracker_allows_after_window() -> None:
    tracker = CooldownTracker()
    first = datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC)
    later = datetime(2026, 10, 12, 10, 0, 11, tzinfo=UTC)
    assert tracker.check(guild_id="g1", player_id="p1", cooldown_seconds=10, now=first) is True
    assert tracker.check(guild_id="g1", player_id="p1", cooldown_seconds=10, now=later) is True


def test_cooldown_tracker_is_per_player_and_guild() -> None:
    tracker = CooldownTracker()
    now = datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC)
    assert tracker.check(guild_id="g1", player_id="p1", cooldown_seconds=10, now=now) is True
    assert tracker.check(guild_id="g1", player_id="p2", cooldown_seconds=10, now=now) is True
    assert tracker.check(guild_id="g2", player_id="p1", cooldown_seconds=10, now=now) is True


class _FakeUsage:
    def __init__(self, total: float) -> None:
        self._total = total

    async def month_total_usd(self, *, guild_id: str, year_month: str) -> float:
        return self._total


class _FakeSettings:
    def __init__(self, cap: float) -> None:
        self._cap = cap

    async def get_value(self, *, scope: str, scope_id: str, skill: str, name: str, default: object) -> object:
        return self._cap


@pytest.mark.asyncio
async def test_check_monthly_cap_not_exceeded() -> None:
    status = await check_monthly_cap(
        usage=_FakeUsage(5.0), settings=_FakeSettings(10.0), guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC),
    )
    assert status.exceeded is False
    assert status.crossed_80_percent is False


@pytest.mark.asyncio
async def test_check_monthly_cap_crossed_80_percent() -> None:
    status = await check_monthly_cap(
        usage=_FakeUsage(8.5), settings=_FakeSettings(10.0), guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC),
    )
    assert status.exceeded is False
    assert status.crossed_80_percent is True


@pytest.mark.asyncio
async def test_check_monthly_cap_exceeded() -> None:
    status = await check_monthly_cap(
        usage=_FakeUsage(10.5), settings=_FakeSettings(10.0), guild_id="g1", now=datetime(2026, 10, 12, tzinfo=UTC),
    )
    assert status.exceeded is True
