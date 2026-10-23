from __future__ import annotations

from decimal import Decimal

import pytest

from services.game import rating_format as rf
from services.game import table_columns as tc


@pytest.mark.parametrize(
    "value,expected",
    [(Decimal(17), "17.00"), (Decimal("17.5"), "17.50"), (17.1, "17.10"), (Decimal("17.0099"), "17.00"), ("16.9", "16.90")],
)
def test_song_rating_is_always_two_places_and_never_rounds_up(value: Decimal | float | str, expected: str) -> None:
    assert rf.song_rating_text(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(Decimal("17.012"), "17.0120"), (Decimal(17), "17.0000"), (17.0024, "17.0024"), (Decimal("17.00249"), "17.0024"), ("16.5", "16.5000")],
)
def test_other_ratings_are_always_four_places(value: Decimal | float | str, expected: str) -> None:
    assert rf.rating_text(value) == expected


def test_unknown_values_use_the_calculation_unavailable_text() -> None:
    assert rf.song_rating_text(None) == "계산 불가"
    assert rf.rating_text(None) == "계산 불가"
    assert rf.rating_text(None, unknown="-") == "-"


def test_delta_has_sign_and_four_places() -> None:
    assert rf.rating_delta_text(Decimal("17.012"), Decimal(17)) == "+0.0120"
    assert rf.rating_delta_text(Decimal(17), Decimal(17)) == "+0.0000"
    assert rf.rating_delta_text(Decimal("16.9"), Decimal(17)) == "-0.1000"


def test_table_song_rating_cell_always_shows_two_places() -> None:
    assert tc.rating_cell(Decimal("17.5")).text == "17.50"
    assert tc.rating_cell(Decimal(17)).text == "17.00"
    assert tc.rating_cell(Decimal("17.31")).raw == pytest.approx(17.31)
