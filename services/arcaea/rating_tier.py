# services/arcaea/rating_tier.py
"""Arcaea 포텐셜 단계(2026-11-28 사용자 제공 표). 경계에 닿으면(`>=`) 그 단계다. 10.00부터 빛이 나고, 12.00부터 별이
붙는다(12.00~12.49 1성, 12.50~12.99 2성, 13.00~ 3성)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PotentialTier:
    base: Decimal
    name: str
    glow: bool = False
    stars: int = 0


POTENTIAL_TIERS: tuple[PotentialTier, ...] = (
    PotentialTier(Decimal(0), "하늘"),
    PotentialTier(Decimal(3), "초록"),
    PotentialTier(Decimal(7), "보라"),
    PotentialTier(Decimal(10), "빛나는 보라", glow=True),
    PotentialTier(Decimal(11), "빛나는 빨강", glow=True),
    PotentialTier(Decimal(12), "빨강 1성", glow=True, stars=1),
    PotentialTier(Decimal("12.5"), "빨강 2성", glow=True, stars=2),
    PotentialTier(Decimal(13), "3성", glow=True, stars=3),
)


def tier_for(potential: Decimal) -> PotentialTier | None:
    """포텐셜이 속한 단계. 0 미만이면 None."""
    found: PotentialTier | None = None
    for tier in POTENTIAL_TIERS:
        if potential >= tier.base:
            found = tier
    return found
