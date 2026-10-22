# services/chunithm/rating_tier.py
"""플레이어 레이팅의 색 단계(명칭)와 이정표 사다리. 단계 경계·별·층은 여기 한 곳이 정하고, 그림(색)과 이정표(기록)가 같이 쓴다.

단계 경계는 게임 안내표 기준이다(2026-10-21 사용자 제공). 백금부터는 0.25마다 별(N성)이 하나씩 붙고(백금은 3성, 무지개·극무지개는
4성까지), 무지개·극무지개에는 소수 첫째 자리마다 층(N층)이 있다. 백금 아래는 단계 이름의 경계만 있고 성·층으로 세지 않는다
(2026-10-22 사용자 결정). 값은 Decimal이고 경계에 닿으면(`>=`) 그 단계다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RatingTier:
    base: Decimal
    name: str
    stars: int = 0  # 0이면 별이 없다(이름의 경계만 있다)
    floors: bool = False  # 소수 첫째 자리마다 N층을 센다


STAR_STEP = Decimal("0.25")
FLOOR_STEP = Decimal("0.1")

# 낮은 단계부터.
RATING_TIERS: tuple[RatingTier, ...] = (
    RatingTier(Decimal(0), "초록"),
    RatingTier(Decimal(4), "주황"),
    RatingTier(Decimal(7), "빨강"),
    RatingTier(Decimal(10), "자주"),
    RatingTier(Decimal(12), "동"),
    RatingTier(Decimal("13.25"), "은"),
    RatingTier(Decimal("14.5"), "금"),
    RatingTier(Decimal("15.25"), "백금", stars=3),
    RatingTier(Decimal(16), "무지개", stars=4, floors=True),
    RatingTier(Decimal(17), "극무지개", stars=4, floors=True),
)

STAR = "star"
FLOOR = "floor"


@dataclass(frozen=True, slots=True)
class Rung:
    """이정표 사다리의 한 칸: 이 레이팅에 닿으면 `label`을 달성한다. `series`는 STAR(0.25마다) 또는 FLOOR(0.1마다)."""

    threshold: Decimal
    label: str
    series: str


def tier_for(rating: Decimal) -> RatingTier | None:
    """레이팅이 속한 단계. 0 미만이면 None."""
    found: RatingTier | None = None
    for tier in RATING_TIERS:
        if rating >= tier.base:
            found = tier
    return found


def _build_ladder() -> tuple[Rung, ...]:
    rungs: list[Rung] = []
    for tier in RATING_TIERS:
        if tier.stars:
            for number in range(tier.stars):
                rungs.append(Rung(tier.base + STAR_STEP * number, f"{tier.name} {number + 1}성", STAR))
        elif tier.base > 0:  # 초록은 시작점이라 이정표가 아니다
            rungs.append(Rung(tier.base, tier.name, STAR))
        if tier.floors:
            # 0층은 단계 시작(1성)과 같은 순간이라 1층부터 센다.
            for number in range(1, 10):
                rungs.append(Rung(tier.base + FLOOR_STEP * number, f"{tier.name} {number}층", FLOOR))
    return tuple(sorted(rungs, key=lambda rung: (rung.threshold, rung.series)))


LADDER: tuple[Rung, ...] = _build_ladder()


def highest_rung_crossed(
    rungs: Sequence[Rung], peak: Decimal | None, current: Decimal | None, *, series: str
) -> Rung | None:
    """`peak < 경계 <= current`인 칸 중 `series`에서 가장 높은 것. 최고치(`peak`)를 모르거나 오르지 않았으면 None.
    peak는 지금까지 닿았던 가장 높은 레이팅이라, 레이팅이 내려갔다 다시 올라와도 이미 닿은 칸은 다시 세지 않는다."""
    if peak is None or current is None or current <= peak:
        return None
    crossed = [rung for rung in rungs if rung.series == series and peak < rung.threshold <= current]
    return max(crossed, key=lambda rung: rung.threshold) if crossed else None


def next_star(rungs: Sequence[Rung], current: Decimal) -> Rung | None:
    """`current` 다음에 닿을 별·단계 칸. 가장 높은 칸을 넘었으면 None."""
    upcoming = [rung for rung in rungs if rung.series == STAR and rung.threshold > current]
    return min(upcoming, key=lambda rung: rung.threshold) if upcoming else None
