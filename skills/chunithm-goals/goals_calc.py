from __future__ import annotations

from decimal import Decimal
from typing import Literal

_BEST_FRAME = 30
_NEW_FRAME = 20
_RATING_DIVISOR = Decimal(50)


def achievement_ratio(current: Decimal, baseline: Decimal, target: Decimal) -> Decimal:
    """(현재-기준)/(목표-기준). 기준과 목표가 같으면 현재값이 목표에 닿았는지로 1 또는 0.
    기준보다 낮아도 음수를 그대로 두고, 목표를 넘어도 자르지 않는다."""
    if target == baseline:
        return Decimal(1) if current >= target else Decimal(0)
    return (current - baseline) / (target - baseline)


def pace(
    ratio: Decimal, elapsed_ratio: Decimal
) -> Literal["ahead", "on_track", "behind"]:
    if ratio > elapsed_ratio:
        return "ahead"
    if ratio == elapsed_ratio:
        return "on_track"
    return "behind"


def rating_gap_to_song_rating_sum(remaining_rating: Decimal) -> Decimal:
    return remaining_rating * _RATING_DIVISOR


def project_rating_with_todos(
    current_best30: list[Decimal],
    current_new20: list[Decimal],
    todo_targets: list[tuple[bool, Decimal]],
) -> Decimal:
    best = list(current_best30)
    new = list(current_new20)
    for is_new, rating in todo_targets:
        (new if is_new else best).append(rating)
    best_top = sorted(best, reverse=True)[:_BEST_FRAME]
    new_top = sorted(new, reverse=True)[:_NEW_FRAME]
    return (sum(best_top, Decimal(0)) + sum(new_top, Decimal(0))) / _RATING_DIVISOR


def folder_progress(total: int, matched: int) -> Decimal | None:
    if total == 0:
        return None
    return Decimal(matched) / Decimal(total)
