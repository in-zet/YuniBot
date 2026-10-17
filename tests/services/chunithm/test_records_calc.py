from datetime import date, datetime
from decimal import Decimal

from services.chunithm.records_calc import (
    allowed_attacks,
    assign_session_date,
    average_rating,
    candidate_upside,
    chart_rating,
    frame_changes,
    match_session_for_chart,
    remaining_to_target,
)


def test_chart_rating_matches_sssp_example():
    # SSS+(1,009,000)에서 상수 12.5짜리 채보: whole = il10000 + 21500
    # il10000 = 125000 -> (125000+21500)//100/100 = 14.65 (vendored calculate_rating)
    assert chart_rating(1_009_000, 12.5) == Decimal("14.65")


def test_chart_rating_below_500k_is_zero():
    assert chart_rating(100_000, 13.0) == Decimal(0)


def test_chart_rating_none_const_is_none():
    """상수를 모르면 "계산 불가"(None)다 — 0으로 취급해 조용히 틀린 값을 내지 않는다
    (사용자 판단, 2026-10-11 최종 리뷰에서 이 테스트의 기존 고정값이 Review Focus의
    "상수를 못 찾으면 None/에러로 드러나야 한다"는 요구와 충돌한다는 걸 확인하고
    바꿨다)."""
    assert chart_rating(1_009_000, None) is None


def test_average_rating_with_any_unknown_is_none():
    assert average_rating([Decimal("15.00"), None, Decimal("16.00")]) is None


def test_average_rating_divides_by_full_length_and_truncates_to_2dp():
    ratings = [Decimal("14.65")] * 30 + [Decimal("14.00")] * 20
    # (14.65*30 + 14.00*20) / 50 = (439.5 + 280) / 50 = 719.5/50 = 14.39
    assert average_rating(ratings) == Decimal("14.39")


def test_average_rating_truncates_not_rounds():
    # 평균이 16.948로 나오는 경우, 버림이면 16.94, 반올림이면 16.95다. 반올림 쪽은
    # 실제 CHUNITHM-NET 표시값과 저장본 검산에서 어긋났던 사례라 버림을 명시적으로 고정한다.
    ratings = [Decimal("16.948")] * 50  # 합=847.40, 평균=16.948(이미 2dp보다 세밀한 입력)
    assert average_rating(ratings) == Decimal("16.94")


def test_allowed_attacks_matches_border_doc_examples():
    # 노트 3,000개: SSS(1,007,500) -> 14.70, SSS+(1,009,000) -> 5.88
    assert allowed_attacks(3000, 1_007_500) == Decimal("14.70")
    assert allowed_attacks(3000, 1_009_000) == Decimal("5.88")


def test_allowed_attacks_truncates_down_not_round():
    # (1,010,000-1,007,500)*3050/510_000 = 2500*3050/510000 = 14.95098...
    assert allowed_attacks(3050, 1_007_500) == Decimal("14.95")


def test_allowed_attacks_none_when_note_count_missing():
    assert allowed_attacks(None, 1_007_500) is None
    assert allowed_attacks(0, 1_007_500) is None


def test_remaining_to_target_already_met_returns_non_positive_and_zero_attacks():
    result = remaining_to_target(3000, current_score=1_008_000, target_score=1_007_500)
    assert result is not None
    remaining_score, attacks = result
    assert remaining_score == -500
    assert attacks == Decimal("0.00")


def test_remaining_to_target_computes_score_and_attacks():
    result = remaining_to_target(3000, current_score=1_000_000, target_score=1_007_500)
    assert result is not None
    remaining_score, attacks = result
    assert remaining_score == 7_500
    # 7500*3000/510000 = 44.117647... -> 44.11
    assert attacks == Decimal("44.11")


def test_remaining_to_target_none_when_note_count_missing():
    assert remaining_to_target(None, current_score=1_000_000, target_score=1_007_500) is None


def test_assign_session_date_before_boundary_belongs_to_previous_day():
    played_at = datetime(2026, 10, 5, 2, 30)  # noqa: DTZ001 - 세션 날짜는 naive 로컬 시각 기준
    assert assign_session_date(played_at) == date(2026, 10, 4)


def test_assign_session_date_at_boundary_belongs_to_same_day():
    played_at = datetime(2026, 10, 5, 4, 0)  # noqa: DTZ001 - 세션 날짜는 naive 로컬 시각 기준
    assert assign_session_date(played_at) == date(2026, 10, 5)


def test_assign_session_date_after_boundary_belongs_to_same_day():
    played_at = datetime(2026, 10, 5, 23, 59)  # noqa: DTZ001 - 세션 날짜는 naive 로컬 시각 기준
    assert assign_session_date(played_at) == date(2026, 10, 5)


def test_assign_session_date_respects_custom_boundary():
    played_at = datetime(2026, 10, 5, 3, 0)  # noqa: DTZ001 - 세션 날짜는 naive 로컬 시각 기준
    assert assign_session_date(played_at, boundary_hour=2) == date(2026, 10, 5)
    assert assign_session_date(played_at, boundary_hour=4) == date(2026, 10, 4)


def test_match_session_for_chart_uses_latest_recent_play():
    recent_by_chart = {
        ("곡A", "MAS"): [
            datetime(2026, 10, 10, 20, 0),  # noqa: DTZ001 - 세션 날짜는 naive 로컬 시각 기준
            datetime(2026, 10, 11, 1, 0),  # noqa: DTZ001 - 세션 날짜는 naive 로컬 시각 기준
        ]
    }
    sessions_by_date = {date(2026, 10, 10): "s1", date(2026, 10, 11): "s2"}
    session_id, estimated = match_session_for_chart(
        ("곡A", "MAS"), recent_by_chart, sessions_by_date, latest_session_id="s2"
    )
    assert (session_id, estimated) == ("s2", False)


def test_match_session_for_chart_falls_back_to_latest_when_not_in_recent():
    session_id, estimated = match_session_for_chart(
        ("곡B", "MAS"), {}, {}, latest_session_id="s2"
    )
    assert (session_id, estimated) == ("s2", True)


def test_match_session_for_chart_returns_none_when_no_fallback_available():
    # recent_by_chart에도 없고 latest_session_id도 없으면 (None, True) — 호출하는
    # 쪽이 오늘 날짜 세션을 새로 만들어 다시 부른다.
    session_id, estimated = match_session_for_chart(
        ("곡C", "MAS"), {}, {}, latest_session_id=None
    )
    assert (session_id, estimated) == (None, True)


def test_match_session_for_chart_falls_back_when_matched_date_missing_from_sessions():
    # recent_by_chart에 채보는 있지만 그 날짜가 sessions_by_date에 없으면(데이터
    # 불일치) latest_session_id로 추정 매칭한다.
    recent_by_chart = {
        ("곡D", "MAS"): [datetime(2026, 10, 9, 12, 0)]  # noqa: DTZ001 - 세션 날짜는 naive 로컬 시각 기준
    }
    session_id, estimated = match_session_for_chart(
        ("곡D", "MAS"), recent_by_chart, {}, latest_session_id="s2"
    )
    assert (session_id, estimated) == ("s2", True)


def test_frame_changes_entries_and_exits():
    entered, exited = frame_changes({("A", "MAS")}, {("B", "MAS")})
    assert entered == {("B", "MAS")}
    assert exited == {("A", "MAS")}


def test_frame_changes_identical_sets_yield_no_changes():
    same = {("A", "MAS"), ("B", "EXP")}
    entered, exited = frame_changes(same, set(same))
    assert entered == set()
    assert exited == set()


def test_candidate_upside_assumes_sss():
    assert candidate_upside(15.0, Decimal("16.50")) == Decimal("0.50")  # 15.0+2.00-16.50


def test_candidate_upside_can_be_negative():
    # 15.0+2.00-17.50 = -0.50 — 음수면 그대로 돌려준다(호출하는 쪽이 0 이하를 거른다).
    assert candidate_upside(15.0, Decimal("17.50")) == Decimal("-0.50")


def test_candidate_upside_respects_custom_target_bonus():
    assert candidate_upside(15.0, Decimal("16.50"), target_bonus=Decimal("3.00")) == Decimal(
        "1.50"
    )
