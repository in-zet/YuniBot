from __future__ import annotations

from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import Literal

_BEST_FRAME = 30
_NEW_FRAME = 20
_RATING_DIVISOR = Decimal(50)
_HUNDREDTH = Decimal("0.01")
# 설계서 "기간과 묶음": 2주 이내는 단기, 3개월 이내는 중기, 그보다 길면 장기.
_SHORT_DAYS = 14
_MID_DAYS = 90

ChartKey = tuple[str, str]  # (곡 제목, 난이도 코드)


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


def elapsed_ratio(created: date, due: date, today: date) -> Decimal:
    """기간 경과율 (오늘-만든 날)/(기한-만든 날), 0~1로 자른다. 기한이 만든 날과 같거나
    앞이면 이미 끝난 기간(1)으로 본다."""
    total = (due - created).days
    if total <= 0:
        return Decimal(1)
    elapsed = (today - created).days
    return min(Decimal(1), max(Decimal(0), Decimal(elapsed) / Decimal(total)))


def rating_gap_to_song_rating_sum(remaining_rating: Decimal) -> Decimal:
    return remaining_rating * _RATING_DIVISOR


def rating_remaining(current: Decimal, target: Decimal) -> Decimal:
    """목표까지 남은 값. 이미 넘었으면 0."""
    return max(Decimal(0), target - current)


def value_change(current: Decimal, previous: Decimal | None) -> Decimal | None:
    """이번 동기화의 변화량. 직전 값이 없으면 None."""
    if previous is None:
        return None
    return current - previous


def truncate2(value: Decimal) -> Decimal:
    """표시용으로 소수 둘째 자리까지 남기고 셋째 자리에서 버린다(레이팅 표시와 같은 규칙)."""
    return value.quantize(_HUNDREDTH, rounding=ROUND_DOWN)


def ratio_percent(ratio: Decimal) -> Decimal:
    """달성률(0~1 비율)을 퍼센트로, 소수 둘째 자리에서 버린다."""
    return truncate2(ratio * 100)


def duration_tag_for(created: date, due: date) -> Literal["short", "mid", "long"]:
    """꼬리표를 안 붙였을 때 기한으로 정하는 기간 꼬리표."""
    days = (due - created).days
    if days <= _SHORT_DAYS:
        return "short"
    if days <= _MID_DAYS:
        return "mid"
    return "long"


def project_rating_with_todos(
    current_best30: list[tuple[ChartKey, Decimal]],
    current_new20: list[tuple[ChartKey, Decimal]],
    todo_targets: list[tuple[ChartKey, bool, Decimal]],
) -> Decimal:
    """곡별 목표를 모두 달성했을 때의 예상 레이팅. 투두 채보가 이미 프레임에 있으면 그
    항목의 레이팅을 바꾸고(더 높을 때만), 없으면 후보로 더한 뒤 각 프레임 상위 30/20을
    다시 고른다."""
    best: dict[ChartKey, Decimal] = dict(current_best30)
    new: dict[ChartKey, Decimal] = dict(current_new20)
    for key, is_new, rating in todo_targets:
        frame = new if is_new else best
        existing = frame.get(key)
        frame[key] = rating if existing is None else max(existing, rating)
    best_top = sorted(best.values(), reverse=True)[:_BEST_FRAME]
    new_top = sorted(new.values(), reverse=True)[:_NEW_FRAME]
    return (sum(best_top, Decimal(0)) + sum(new_top, Decimal(0))) / _RATING_DIVISOR


def folder_progress(total: int, matched: int) -> Decimal | None:
    if total == 0:
        return None
    return Decimal(matched) / Decimal(total)
