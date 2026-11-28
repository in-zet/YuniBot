# services/arcaea/rating.py
"""Arcaea 곡 포텐셜(7.0.0, 2026-08-27 개정). 근거는 팬 위키 "Gameplay and scoring"이고, Arcaea Online이 준 베스트 50의
곡별 값과 전체 포텐셜로 검산했다(`docs/reference/arcaea-online/README.md`)."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

# 클리어(TRACK LOST가 아님)하면 곡 포텐셜에 더한다. TRACK LOST는 프로필의 클리어 램프 보너스가 도로 뺀다.
CLEAR_BONUS = Decimal("0.2")
_PLACES = Decimal("0.0001")


def score_modifier(score: int) -> Decimal:
    """점수 보정. 10,000,000점 이상 +2, 9,800,000점 이상 +1 + 200,000점마다 1, 그 아래는 9,500,000점 기준 300,000점마다 1."""
    if score >= 10_000_000:
        return Decimal(2)
    if score >= 9_800_000:
        return 1 + Decimal(score - 9_800_000) / 200_000
    return Decimal(score - 9_500_000) / 300_000


def chart_rating(score: int, const: float | None) -> Decimal | None:
    """클리어했다고 본 곡 포텐셜(넷째 자리에서 버림). 상수를 모르면 None("계산 불가"). 0 밑으로는 안 내려간다."""
    if const is None:
        return None
    value = max(Decimal(0), Decimal(str(const)) + score_modifier(score)) + CLEAR_BONUS
    return value.quantize(_PLACES, rounding=ROUND_DOWN)
