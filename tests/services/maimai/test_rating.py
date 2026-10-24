from decimal import Decimal

import pytest

from services.maimai.rating import chart_rating, coefficient


@pytest.mark.parametrize(
    ("achievement", "const", "expected"),
    [
        (1_005_000, 14.0, 315),  # SSS+ 22.4 × 14.0 × 1.005 = 315.17
        (1_005_000, 13.0, 292),  # 22.4 × 13.0 × 1.005 = 292.65
        (1_000_000, 13.0, 280),  # SSS 21.6 × 13.0 = 280.8
        (995_000, 13.0, 272),  # SS+ 21.1 × 13.0 × 0.995 = 272.92
        (970_000, 12.0, 232),  # S 20.0 × 12.0 × 0.97 = 232.8
        (0, 15.0, 0),
    ],
)
def test_chart_rating_floors_to_integer(achievement: int, const: float, expected: int) -> None:
    assert chart_rating(achievement, const) == Decimal(expected)


def test_achievement_above_cap_counts_as_100_5() -> None:
    assert chart_rating(1_010_000, 14.0) == chart_rating(1_005_000, 14.0)


def test_unknown_const_is_not_computable() -> None:
    assert chart_rating(1_000_000, None) is None


@pytest.mark.parametrize(
    ("achievement", "expected"),
    [
        (99_999, "0"), (100_000, "1.6"), (799_998, "12.0"), (799_999, "12.8"), (800_000, "13.6"),
        (969_998, "16.8"), (969_999, "17.6"), (970_000, "20.0"), (989_999, "20.6"), (990_000, "20.8"),
        (999_998, "21.1"), (999_999, "21.4"), (1_000_000, "21.6"), (1_004_998, "21.6"),
        (1_004_999, "22.2"), (1_005_000, "22.4"),
    ],
)
def test_coefficient_boundaries(achievement: int, expected: str) -> None:
    assert coefficient(achievement) == Decimal(expected)
