from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from chuni_penguin.calculation.rating import calculate_rating

_MAX_SCORE = 1_010_000
_ATTACK_DEDUCTION_DENOMINATOR = 510_000
_ONE_DAY = timedelta(days=1)


def chart_rating(score: int, const: float | None) -> Decimal:
    """채보 하나의 레이팅. vendored calculate_rating을 그대로 감싼다 — 그 함수가
    이미 둘째 자리에서 버린다(1/10000 단위 정수를 //100으로 두 자리 버린 뒤 /100).
    975,000점 미만 구간(900k/800k/500k 보간, 500k 미만은 0)도 같은 함수 안에 있다
    (사용자 확인: vendored 실제 공식 사용, 재구현하지 않는다).

    둘째 자리에서 버리는 것이 맞다 — 넷째 자리까지 들고 있다가 합산 뒤에만 버리면
    안 된다. 실제 저장본(베스트30+신곡20+프로필, 같은 로그인 시점)으로 검산했을 때
    곡마다 둘째 자리 버림 후 합산해야 표시 레이팅과 맞았고, 넷째 자리를 유지하면
    어긋났다. docs/design/skills/chunithm-report.md도 이 정밀도로 고쳤다."""
    return calculate_rating(score, const)


def average_rating(ratings: Sequence[Decimal]) -> Decimal:
    """베스트30 평균, 신곡20 평균, 전체 레이팅(50으로 나눔)에 공통으로 쓴다. 입력은
    이미 chart_rating()으로 둘째 자리까지 버려진 값들이고, 합산한 평균도 둘째
    자리에서 버린다(위 chart_rating 설명 참고 — 더 가는 단위를 유지할 근거가 없다)."""
    if not ratings:
        return Decimal("0.00")
    total = sum(ratings, Decimal(0))
    return (total / len(ratings)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def allowed_attacks(note_count: int | None, target_score: int) -> Decimal | None:
    """저스티스 0, 미스 0 기준의 허용 어택 개수. 노트 수가 없으면(계산 불가) None."""
    if not note_count:
        return None
    deduction = Decimal(_MAX_SCORE - target_score) * note_count
    return (deduction / Decimal(_ATTACK_DEDUCTION_DENOMINATOR)).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )


def remaining_to_target(
    note_count: int | None, current_score: int, target_score: int
) -> tuple[int, Decimal] | None:
    """목표까지 남은 점수와 어택. 노트 수가 없으면 None. 이미 목표를 넘었으면
    (음수 또는 0 남은 점수, 0.00어택)을 돌려준다 — 호출하는 쪽이 "이미 달성"으로
    말로 바꾼다."""
    if not note_count:
        return None
    remaining_score = target_score - current_score
    if remaining_score <= 0:
        return remaining_score, Decimal("0.00")
    attacks = (
        Decimal(remaining_score) * note_count / Decimal(_ATTACK_DEDUCTION_DENOMINATOR)
    ).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return remaining_score, attacks


def assign_session_date(played_at: datetime, *, boundary_hour: int = 4) -> date:
    """자정을 넘긴 플레이는 날짜 경계(기본 새벽 4시) 전까지 전날 세션에 붙는다
    (chunithm-session.md). 경계 시각 자체는 "그날"에 속한다(>=)."""
    if played_at.hour < boundary_hour:
        return (played_at - _ONE_DAY).date()
    return played_at.date()
