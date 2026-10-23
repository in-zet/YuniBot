# skills/goals/tests/test_goals_calc.py
from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

_CALC_PY = Path(__file__).parent.parent / "goals_calc.py"


def _load_calc() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_chunithm_goals_calc_under_test", _CALC_PY
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calc = _load_calc()


def D(value: str) -> Decimal:
    return Decimal(value)


def test_achievement_ratio_is_zero_at_baseline() -> None:
    assert calc.achievement_ratio(D("13.0"), D("13.0"), D("14.0")) == D("0")


def test_achievement_ratio_is_one_at_target() -> None:
    assert calc.achievement_ratio(D("14.0"), D("13.0"), D("14.0")) == D("1")


def test_achievement_ratio_is_midpoint() -> None:
    assert calc.achievement_ratio(D("13.5"), D("13.0"), D("14.0")) == D("0.5")


def test_achievement_ratio_allows_negative_when_below_baseline() -> None:
    assert calc.achievement_ratio(D("12.5"), D("13.0"), D("14.0")) == D("-0.5")


def test_achievement_ratio_allows_values_above_one() -> None:
    assert calc.achievement_ratio(D("14.5"), D("13.0"), D("14.0")) == D("1.5")


def test_achievement_ratio_when_target_equals_baseline_is_done_or_not() -> None:
    assert calc.achievement_ratio(D("13.0"), D("13.0"), D("13.0")) == D("1")
    assert calc.achievement_ratio(D("13.5"), D("13.0"), D("13.0")) == D("1")
    assert calc.achievement_ratio(D("12.9"), D("13.0"), D("13.0")) == D("0")


def test_pace_ahead_when_ratio_exceeds_elapsed() -> None:
    assert calc.pace(D("0.6"), D("0.5")) == "ahead"


def test_pace_on_track_when_equal() -> None:
    assert calc.pace(D("0.5"), D("0.5")) == "on_track"


def test_pace_behind_when_ratio_below_elapsed() -> None:
    assert calc.pace(D("0.3"), D("0.5")) == "behind"


def test_rating_gap_to_song_rating_sum_multiplies_by_fifty() -> None:
    assert calc.rating_gap_to_song_rating_sum(D("0.25")) == D("12.5")
    assert calc.rating_gap_to_song_rating_sum(D("1")) == D("50")


def _frame(ratings: list[Decimal], prefix: str) -> list[tuple[tuple[str, str], Decimal]]:
    return [((f"{prefix}{i}", "MAS"), r) for i, r in enumerate(ratings)]


def test_projection_adds_out_of_frame_song_and_raises_average() -> None:
    best30 = _frame([D("13.00")] * 30, "b")
    new20 = _frame([D("12.00")] * 20, "n")
    # 베스트30 밖의 곡이 13.50으로 들어오면 최하위 13.00 하나를 밀어낸다.
    projected = calc.project_rating_with_todos(best30, new20, [(("밖", "MAS"), False, D("13.50"))])
    assert projected == D("12.61")


def test_projection_ignores_song_below_frame() -> None:
    best30 = _frame([D("13.00")] * 30, "b")
    new20 = _frame([D("12.00")] * 20, "n")
    projected = calc.project_rating_with_todos(best30, new20, [(("밖", "MAS"), False, D("5.00"))])
    assert projected == D("12.60")


def test_projection_raises_only_in_frame_song_score() -> None:
    best30 = _frame([D("13.00")] * 30, "b")
    new20 = _frame([D("12.00")] * 20, "n")
    # 이미 프레임 안에 있는 곡(b0, 13.00)이 14.00이 되면 그 차이(+1.00)만큼만 오른다.
    projected = calc.project_rating_with_todos(best30, new20, [(("b0", "MAS"), False, D("14.00"))])
    assert projected == D("12.62")


def test_projection_replaces_in_frame_chart_instead_of_adding_it_again() -> None:
    """I2: 15.00 한 곡 + 13.00 스물아홉 곡. 그 15.00 채보를 15.50으로 올리는 투두는
    +0.50(곡 레이팅 합) → 전체 +0.01이다. 옛 값을 그대로 둔 채 15.50을 덧붙이면 최하위
    13.00이 밀려나 +2.50(→ +0.05)으로 잘못 계산된다."""
    best30 = [(("높은곡", "MAS"), D("15.00")), *_frame([D("13.00")] * 29, "b")]
    projected = calc.project_rating_with_todos(best30, [], [(("높은곡", "MAS"), False, D("15.50"))])
    # (15.50 + 13.00 * 29) / 50 = 392.50 / 50
    assert projected == D("7.85")


def test_projection_keeps_higher_current_rating_when_todo_is_lower() -> None:
    best30 = [(("곡", "MAS"), D("15.00")), *_frame([D("13.00")] * 29, "b")]
    projected = calc.project_rating_with_todos(best30, [], [(("곡", "MAS"), False, D("14.00"))])
    assert projected == D("7.84")


def test_projection_new_side_todo_replaces_lowest_new_song() -> None:
    best30 = _frame([D("13.00")] * 30, "b")
    new20 = _frame([D("12.00")] * 20, "n")
    projected = calc.project_rating_with_todos(best30, new20, [(("신곡", "MAS"), True, D("13.00"))])
    assert projected == D("12.62")


def test_projection_with_no_todos_is_current_rating() -> None:
    best30 = _frame([D("13.00")] * 30, "b")
    new20 = _frame([D("12.00")] * 20, "n")
    assert calc.project_rating_with_todos(best30, new20, []) == D("12.60")


def test_folder_progress_is_matched_over_total() -> None:
    assert calc.folder_progress(10, 3) == D("0.3")
    assert calc.folder_progress(4, 4) == D("1")


def test_folder_progress_is_none_when_folder_is_empty() -> None:
    assert calc.folder_progress(0, 0) is None


def test_rating_remaining_is_target_minus_current_and_never_negative() -> None:
    assert calc.rating_remaining(D("16.81"), D("17.00")) == D("0.19")
    assert calc.rating_remaining(D("17.10"), D("17.00")) == D("0")


def test_value_change_is_none_without_previous() -> None:
    assert calc.value_change(D("16.85"), D("16.81")) == D("0.04")
    assert calc.value_change(D("16.85"), None) is None


def test_elapsed_ratio_between_created_and_due() -> None:
    from datetime import date

    assert calc.elapsed_ratio(date(2026, 10, 1), date(2026, 10, 11), date(2026, 10, 6)) == D("0.5")
    assert calc.elapsed_ratio(date(2026, 10, 1), date(2026, 10, 11), date(2026, 10, 20)) == D("1")
    assert calc.elapsed_ratio(date(2026, 10, 1), date(2026, 10, 11), date(2026, 9, 20)) == D("0")
    # 기한이 만든 날과 같거나 앞이면 경과율은 이미 끝난 것(1)으로 본다
    assert calc.elapsed_ratio(date(2026, 10, 1), date(2026, 10, 1), date(2026, 10, 1)) == D("1")


def test_ratio_percent_truncates_to_two_decimals() -> None:
    assert calc.ratio_percent(D("0.123456")) == D("12.34")
    assert calc.ratio_percent(D("1")) == D("100.00")
    assert calc.ratio_percent(D("-0.0507")) == D("-5.07")


def test_truncate4_keeps_four_decimals_and_drops_the_rest() -> None:
    assert calc.truncate4(D("0.02749")) == D("0.0274")
    assert str(calc.truncate4(D("0.1"))) == "0.1000"


def test_rating_remaining_to_17_keeps_four_decimals() -> None:
    remaining = calc.rating_remaining(D("16.9726"), D("17"))

    assert calc.truncate4(remaining) == D("0.0274")
    assert calc.truncate4(calc.rating_gap_to_song_rating_sum(remaining)) == D("1.3700")


def test_truncate2_drops_third_decimal() -> None:
    assert calc.truncate2(D("16.8199")) == D("16.81")
    assert calc.truncate2(D("7.85")) == D("7.85")


def test_duration_tag_from_due_date() -> None:
    from datetime import date

    created = date(2026, 10, 1)
    assert calc.duration_tag_for(created, date(2026, 10, 15)) == "short"  # 14일 = 2주 이내
    assert calc.duration_tag_for(created, date(2026, 10, 16)) == "mid"
    assert calc.duration_tag_for(created, date(2026, 12, 30)) == "mid"  # 90일 이내
    assert calc.duration_tag_for(created, date(2027, 1, 1)) == "long"
