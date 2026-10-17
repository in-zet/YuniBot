# skills/chunithm-goals/tests/test_goals_calc.py
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


def test_projection_adds_out_of_frame_song_and_raises_average() -> None:
    best30 = [D("13.00")] * 30
    new20 = [D("12.00")] * 20
    # 베스트30 밖의 곡이 13.50으로 들어오면 최하위 13.00 하나를 밀어낸다.
    projected = calc.project_rating_with_todos(best30, new20, [(False, D("13.50"))])
    assert projected == D("12.61")


def test_projection_ignores_song_below_frame() -> None:
    best30 = [D("13.00")] * 30
    new20 = [D("12.00")] * 20
    projected = calc.project_rating_with_todos(best30, new20, [(False, D("5.00"))])
    assert projected == D("12.60")


def test_projection_raises_only_in_frame_song_score() -> None:
    best30 = [D("13.00")] * 30
    new20 = [D("12.00")] * 20
    # 이미 프레임 안에 있는 곡(같은 13.00)이 14.00이 되면 그 차이만큼 오른다.
    projected = calc.project_rating_with_todos(best30, new20, [(False, D("14.00"))])
    assert projected == D("12.62")


def test_projection_new_side_todo_replaces_lowest_new_song() -> None:
    best30 = [D("13.00")] * 30
    new20 = [D("12.00")] * 20
    projected = calc.project_rating_with_todos(best30, new20, [(True, D("13.00"))])
    assert projected == D("12.62")


def test_projection_with_no_todos_is_current_rating() -> None:
    best30 = [D("13.00")] * 30
    new20 = [D("12.00")] * 20
    assert calc.project_rating_with_todos(best30, new20, []) == D("12.60")


def test_folder_progress_is_matched_over_total() -> None:
    assert calc.folder_progress(10, 3) == D("0.3")
    assert calc.folder_progress(4, 4) == D("1")


def test_folder_progress_is_none_when_folder_is_empty() -> None:
    assert calc.folder_progress(0, 0) is None
