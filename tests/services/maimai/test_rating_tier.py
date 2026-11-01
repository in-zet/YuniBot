from decimal import Decimal

import pytest

from services.maimai.rating_tier import stars_for, tier_for


@pytest.mark.parametrize(
    "rating,name,stars",
    [(0, "하양", 0), (999, "하양", 0), (1000, "파랑", 0), (9999, "빨강", 0), (12999, "동", 0), (13999, "은", 0),
     (14000, "금", 1), (14249, "금", 1), (14250, "금", 2), (14499, "금", 2),
     (14500, "백금", 1), (14749, "백금", 1), (14750, "백금", 2), (14999, "백금", 2),
     (15000, "무지개", 1), (15249, "무지개", 1), (15250, "무지개", 2), (15500, "무지개", 3),
     (15750, "무지개", 4), (15999, "무지개", 4),
     (16000, "극무지개", 1), (16250, "극무지개", 2), (16500, "극무지개", 3), (16749, "극무지개", 3),
     (16750, "극무지개", 4), (17500, "극무지개", 4)],
)
def test_tier_and_stars_follow_the_game_table(rating: int, name: str, stars: int) -> None:
    tier = tier_for(Decimal(rating))

    assert tier is not None and tier.name == name
    assert stars_for(Decimal(rating)) == stars


def test_negative_rating_has_no_tier_and_no_stars() -> None:
    assert tier_for(Decimal(-1)) is None
    assert stars_for(Decimal(-1)) == 0
