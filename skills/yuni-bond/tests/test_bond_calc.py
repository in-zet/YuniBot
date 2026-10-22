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


def test_remaining_rounds_the_remaining_up() -> None:
    assert calc.remaining_to(Decimal("16.50"), Decimal("16.26")) == Decimal("0.2400")
    # 모자라게 말하지 않도록 넷째 자리에서 올린다: 0.24009가 남았으면 0.2401.
    assert calc.remaining_to(Decimal("16.50"), Decimal("16.25991")) == Decimal("0.2401")
    assert str(calc.remaining_to(Decimal("16.50"), Decimal("16.26"))) == "0.2400"


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


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (None, "FULL COMBO", ["FC"]),
        (None, "ALL JUSTICE", ["FC", "AJ"]),  # 올저는 풀콤을 포함한다
        ("NONE", "AJC", ["FC", "AJ"]),  # AJC는 따로 세지 않고 AJ에 든다
        ("FULL COMBO", "ALL JUSTICE", ["AJ"]),
        ("ALL JUSTICE", "AJC", []),
        ("FULL COMBO", "FULL COMBO", []),
        ("ALL JUSTICE", "FULL COMBO", []),
        (None, "NONE", []),
        (None, "", []),
    ],
)
def test_lamp_tiers_reached(old: str | None, new: str, expected: list[str]) -> None:
    assert calc.lamp_tiers_reached(old, new) == expected


@pytest.mark.parametrize(
    ("rank", "lamp", "tier", "expected"),
    [
        ("SSS", "NONE", "SSS", True),
        ("SSS+", "NONE", "SSS", True),  # 트플플은 트리플도 만족한다
        ("SSS", "NONE", "SSS+", False),
        ("SS+", "FULL COMBO", "SSS", False),
        ("S", "FULL COMBO", "FC", True),
        ("S", "ALL JUSTICE", "FC", True),
        ("S", "FULL COMBO", "AJ", False),
        ("S", "AJC", "AJ", True),
        (None, None, "FC", False),
    ],
)
def test_holds_tier(rank: str | None, lamp: str | None, tier: str, expected: bool) -> None:
    assert calc.holds_tier(rank, lamp, tier) is expected


def test_const_text_always_has_one_decimal_place() -> None:
    assert calc.const_text(15.0) == "15.0"
    assert calc.const_text(15.4) == "15.4"


def test_tier_labels_use_the_players_words() -> None:
    assert calc.TIER_LABELS == {"SSS": "트리플", "SSS+": "트플플", "FC": "풀콤", "AJ": "올저"}


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
