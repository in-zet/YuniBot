# services/maimai/rating_tier.py
"""maimai DX 레이팅의 색 단계(명칭)와 별. 경계는 게임의 레이팅 단계표 기준이다(2026-11-01 사용자 제공 그림). 값은 정수 레이팅이고
경계에 닿으면(`>=`) 그 단계다. CHUNITHM과 별이 다르다 — 금부터 250마다 별이 하나씩 붙고(금·백금은 2성, 무지개·극무지개는 4성까지),
극무지개는 16000부터 무지개와 따로 센다. 하양~보라·동·은은 별이 없다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MaimaiRatingTier:
    base: int
    name: str
    stars: int = 0  # 0이면 별이 없다


STAR_STEP = 250

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
    MaimaiRatingTier(14000, "금", stars=2),
    MaimaiRatingTier(14500, "백금", stars=2),
    MaimaiRatingTier(15000, "무지개", stars=4),
    MaimaiRatingTier(16000, "극무지개", stars=4),
)


def tier_for(rating: Decimal) -> MaimaiRatingTier | None:
    """레이팅이 속한 단계. 0 미만이면 None."""
    found: MaimaiRatingTier | None = None
    for tier in RATING_TIERS:
        if rating >= tier.base:
            found = tier
    return found


def stars_for(rating: Decimal) -> int:
    """그 레이팅의 별 개수. 별이 없는 단계거나 0 미만이면 0. 단계 안에서 250마다 하나씩 늘고 단계의 별 수를 넘지 않는다
    (예: 금 14000~14249는 1성, 14250~14499는 2성 / 무지개 15750~15999는 4성)."""
    tier = tier_for(rating)
    if tier is None or tier.stars == 0:
        return 0
    return min(int((rating - tier.base) // STAR_STEP) + 1, tier.stars)
