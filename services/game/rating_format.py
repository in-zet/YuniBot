# services/game/rating_format.py
"""레이팅 글자 모양. 곡 레이팅은 항상 소수 둘째 자리까지(17.00), 그 밖의 레이팅(플레이어 레이팅·베스트/신곡 평균·그 변화량)은
항상 넷째 자리까지(17.0120)로 쓴다(2026-10-21 결정). 값은 버림이라 자리를 채우거나 넘는 자리를 잘라낼 뿐 올리지 않는다.
저장·계산 경로에서 float을 거치면 0이 빠진 값(17.012)으로 돌아오므로 보일 때 한 번 더 맞춘다."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

UNKNOWN = "계산 불가"
SONG_RATING_PLACES = 2
RATING_PLACES = 4


def _fixed(value: Decimal | float | str, places: int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)


def song_rating_text(value: Decimal | float | str | None, *, unknown: str = UNKNOWN) -> str:
    """곡 레이팅: 소수 둘째 자리까지 항상."""
    return unknown if value is None else f"{_fixed(value, SONG_RATING_PLACES):f}"


def rating_text(value: Decimal | float | str | None, *, unknown: str = UNKNOWN) -> str:
    """플레이어 레이팅·베스트/신곡 평균: 소수 넷째 자리까지 항상."""
    return unknown if value is None else f"{_fixed(value, RATING_PLACES):f}"


def rating_delta_text(current: Decimal | float | str, previous: Decimal | float | str) -> str:
    """레이팅 변화량: 부호를 붙여 소수 넷째 자리까지 항상(+0.0000)."""
    difference = _fixed(current, RATING_PLACES) - _fixed(previous, RATING_PLACES)
    return f"{difference:+f}"
