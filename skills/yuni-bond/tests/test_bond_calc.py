# skills/yuni-bond/tests/test_bond_calc.py
from __future__ import annotations

import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

_PATH = Path(__file__).parent.parent / "bond_calc.py"
_spec = importlib.util.spec_from_file_location("_yuni_bond_calc_under_test", _PATH)
assert _spec is not None and _spec.loader is not None
calc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calc)


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        ("16.20", "16.26", "16.25"),
        ("16.24", "16.25", "16.25"),  # 경계에 딱 닿아도 넘은 것이다
        ("16.25", "16.30", None),  # 이미 경계 위에 있었다
        ("16.20", "16.24", None),
        ("16.20", "16.80", "16.75"),  # 여러 경계를 한 번에 넘으면 가장 높은 것
        ("16.20", "17.00", "17.00"),
        ("16.30", "16.20", None),  # 내려갔다
        ("16.30", "16.30", None),
    ],
)
def test_highest_milestone_crossed(previous: str, current: str, expected: str | None) -> None:
    result = calc.highest_milestone_crossed(Decimal(previous), Decimal(current))
    assert result == (Decimal(expected) if expected is not None else None)


def test_milestone_is_unknown_when_a_rating_is_unknown() -> None:
    assert calc.highest_milestone_crossed(None, Decimal("16.5")) is None
    assert calc.highest_milestone_crossed(Decimal("16.5"), None) is None


def test_next_milestone_and_remaining_round_the_remaining_up() -> None:
    assert calc.next_milestone(Decimal("16.26")) == Decimal("16.50")
    assert calc.next_milestone(Decimal("16.25")) == Decimal("16.50")
    assert calc.remaining_to(Decimal("16.50"), Decimal("16.26")) == Decimal("0.24")
    # 모자라게 말하지 않도록 올린다: 0.2401이 남았으면 0.25.
    assert calc.remaining_to(Decimal("16.50"), Decimal("16.2599")) == Decimal("0.25")


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (None, "SSS", ["SSS"]),
        ("SS+", "SSS", ["SSS"]),
        ("SS+", "SSS+", ["SSS", "SSS+"]),
        ("SSS", "SSS+", ["SSS+"]),
        ("SSS", "SSS", []),
        ("SSS+", "SSS+", []),
        ("S", "SS", []),
        ("SSS", "SS+", []),
    ],
)
def test_rank_tiers_reached(old: str | None, new: str, expected: list[str]) -> None:
    assert calc.rank_tiers_reached(old, new) == expected


def test_days_between_never_goes_negative() -> None:
    assert calc.days_between("2026-10-01", "2026-10-06") == 5
    assert calc.days_between("2026-10-06T12:00:00+00:00", "2026-10-06") == 0
    assert calc.days_between("2026-10-06", "2026-10-01") == 0


def test_date_label_adds_the_year_only_when_it_is_not_this_year() -> None:
    today = date(2026, 10, 19)
    assert calc.date_label("2026-10-05", today=today) == "10월 5일"
    assert calc.date_label("2025-12-03", today=today) == "2025년 12월 3일"


def test_clean_promise_folds_whitespace_and_caps_length() -> None:
    assert calc.clean_promise("크로노\n  연습할게") == "크로노 연습할게"
    assert len(calc.clean_promise("가" * 500)) == calc.PROMISE_MAX_CHARS
