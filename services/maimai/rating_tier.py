# services/maimai/rating_tier.py
"""maimai DX 레이팅의 색 단계(명칭). 경계는 게임의 레이팅 배경색(흰색~무지개) 기준이며, 값은 정수 레이팅이다.
경계에 닿으면(`>=`) 그 단계다. (2026-11-01 — 표시값 8564가 빨강이던 실제 페이지로 한 곳만 확인했다.)"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MaimaiRatingTier:
    base: int
    name: str


# 낮은 단계부터.
RATING_TIERS: tuple[MaimaiRatingTier, ...] = (
    MaimaiRatingTier(0, "하양"),
    MaimaiRatingTier(1000, "파랑"),
    MaimaiRatingTier(2000, "초록"),
    MaimaiRatingTier(4000, "노랑"),
    MaimaiRatingTier(7000, "빨강"),
    MaimaiRatingTier(10000, "보라"),
    MaimaiRatingTier(12000, "동"),
    MaimaiRatingTier(13000, "은"),
    MaimaiRatingTier(14000, "금"),
    MaimaiRatingTier(14500, "백금"),
    MaimaiRatingTier(15000, "무지개"),
)


def tier_for(rating: Decimal) -> MaimaiRatingTier | None:
    """레이팅이 속한 단계. 0 미만이면 None."""
    found: MaimaiRatingTier | None = None
    for tier in RATING_TIERS:
        if rating >= tier.base:
            found = tier
    return found
