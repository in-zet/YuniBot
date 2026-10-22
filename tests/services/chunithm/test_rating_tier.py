from __future__ import annotations

from decimal import Decimal

import pytest

from services.chunithm.rating_tier import (
    FLOOR,
    LADDER,
    STAR,
    highest_rung_crossed,
    next_star,
    tier_for,
)


def _d(value: str) -> Decimal:
    return Decimal(value)


@pytest.mark.parametrize(
    ("rating", "name"),
    [("0", "초록"), ("3.99", "초록"), ("4", "주황"), ("14.49", "은"), ("14.5", "금"), ("15.24", "금"), ("15.25", "백금"),
     ("15.99", "백금"), ("16.00", "무지개"), ("16.9999", "무지개"), ("17.00", "극무지개"), ("17.0024", "극무지개")],
)
def test_tier_for_follows_the_chunithm_table(rating: str, name: str) -> None:
    tier = tier_for(_d(rating))
    assert tier is not None and tier.name == name


def test_tier_for_is_none_below_zero() -> None:
    assert tier_for(_d("-0.01")) is None


def _label_at(threshold: str, series: str) -> str:
    return next(rung.label for rung in LADDER if rung.threshold == _d(threshold) and rung.series == series)


def test_stars_are_every_quarter_from_platinum_with_the_user_examples() -> None:
    assert _label_at("15.25", STAR) == "백금 1성"
    assert _label_at("15.5", STAR) == "백금 2성"
    assert _label_at("15.75", STAR) == "백금 3성"
    assert _label_at("16.00", STAR) == "무지개 1성"
    assert _label_at("16.75", STAR) == "무지개 4성"
    assert _label_at("17.00", STAR) == "극무지개 1성"
    assert _label_at("17.25", STAR) == "극무지개 2성"
    assert _label_at("17.75", STAR) == "극무지개 4성"


def test_platinum_has_three_stars_and_rainbows_four() -> None:
    stars = [rung.label for rung in LADDER if rung.series == STAR and "성" in rung.label]
    assert [label for label in stars if label.startswith("백금")] == ["백금 1성", "백금 2성", "백금 3성"]
    assert len([label for label in stars if label.startswith("무지개")]) == 4
    assert len([label for label in stars if label.startswith("극무지개")]) == 4


def test_below_platinum_only_tier_names_exist_no_stars_or_floors() -> None:
    below = [rung for rung in LADDER if rung.threshold < _d("15.25")]
    assert [rung.label for rung in below] == ["주황", "빨강", "자주", "동", "은", "금"]
    assert {rung.series for rung in below} == {STAR}


def test_floors_are_only_in_rainbow_and_extreme_rainbow_and_skip_floor_zero() -> None:
    floors = [rung for rung in LADDER if rung.series == FLOOR]
    assert len(floors) == 18
    assert _label_at("16.7", FLOOR) == "무지개 7층"
    assert _label_at("17.2", FLOOR) == "극무지개 2층"
    assert all(rung.threshold >= _d("16.1") for rung in floors)
    assert not any(rung.label.endswith("0층") for rung in floors)


def test_highest_rung_crossed_picks_the_highest_per_series() -> None:
    star = highest_rung_crossed(LADDER, _d("16.10"), _d("16.80"), series=STAR)
    floor = highest_rung_crossed(LADDER, _d("16.10"), _d("16.80"), series=FLOOR)
    assert star is not None and star.label == "무지개 4성"
    assert floor is not None and floor.label == "무지개 8층"


def test_highest_rung_crossed_counts_exact_threshold_and_ignores_already_reached() -> None:
    hit = highest_rung_crossed(LADDER, _d("16.9999"), _d("17.0000"), series=STAR)
    assert hit is not None and hit.label == "극무지개 1성"
    assert highest_rung_crossed(LADDER, _d("17.30"), _d("17.30"), series=STAR) is None
    assert highest_rung_crossed(LADDER, _d("17.30"), _d("16.50"), series=STAR) is None
    assert highest_rung_crossed(LADDER, None, _d("17.0"), series=STAR) is None


def test_after_a_rating_drop_already_reached_rungs_are_not_crossed_again() -> None:
    """최고치가 17.30이면 레이팅이 16.4로 내려갔다 17.0으로 올라와도 이미 닿은 칸이다."""
    peak = _d("17.30")
    assert highest_rung_crossed(LADDER, peak, _d("17.00"), series=STAR) is None
    assert highest_rung_crossed(LADDER, peak, _d("17.00"), series=FLOOR) is None
    higher = highest_rung_crossed(LADDER, peak, _d("17.55"), series=STAR)
    assert higher is not None and higher.label == "극무지개 3성"


def test_next_star_is_the_next_quarter_and_none_at_the_top() -> None:
    nxt = next_star(LADDER, _d("16.80"))
    assert nxt is not None and nxt.label == "극무지개 1성" and nxt.threshold == _d("17")
    nxt = next_star(LADDER, _d("14.0"))
    assert nxt is not None and nxt.label == "금"
    assert next_star(LADDER, _d("17.80")) is None
