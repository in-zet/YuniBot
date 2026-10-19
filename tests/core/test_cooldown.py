# tests/core/test_cooldown.py
import time
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


@pytest.mark.asyncio
async def test_check_monthly_cap_uses_utc_month_not_server_local_month(monkeypatch: pytest.MonkeyPatch) -> None:
    """core/usage.py는 기록 시각을 UTC로 저장한다. 서버가 UTC+9(KST)면 로컬 11월 1일 00:30은
    UTC로 아직 10월 31일이라 "이번 달" 집계도 UTC 기준 10월이어야 한다(그렇지 않으면 월
    경계 몇 시간 동안 비용 상한 집행이 실제 저장된 달과 어긋난다)."""
    monkeypatch.setenv("TZ", "Asia/Seoul")
    time.tzset()
    try:
        captured: dict[str, str] = {}

        class _CapturingUsage:
            async def month_total_usd(self, *, guild_id: str, year_month: str) -> float:
                captured["year_month"] = year_month
                return 0.0

        naive_local_now = datetime(2026, 11, 1, 0, 30, 0)  # noqa: DTZ001 - 실제 호출부(core/mention.py)처럼 일부러 naive(서버 로컬)다. KST 기준, UTC로는 10월 31일 15:30
        await check_monthly_cap(
            usage=_CapturingUsage(), settings=_FakeSettings(10.0), guild_id="g1", now=naive_local_now,
        )
        assert captured["year_month"] == "2026-10"
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()
