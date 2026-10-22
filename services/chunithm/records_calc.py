from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from chuni_penguin.calculation.rating import calculate_rating

_MAX_SCORE = 1_010_000
_ATTACK_DEDUCTION_DENOMINATOR = 510_000
_ONE_DAY = timedelta(days=1)


def chart_rating(score: int, const: float | None) -> Decimal | None:
    """채보 하나의 레이팅. 상수를 모르면 None("계산 불가")을 돌려준다 — vendored
    calculate_rating에 None을 그대로 넘기면 internal_level=0으로 취급해 조용히
    틀린(하지만 그럴듯한) 값을 낸다. 숫자는 코드가 계산하되, 모르는 것은 모른다고
    드러내야 한다(CLAUDE.md 숫자 규칙).

    const가 있으면 vendored calculate_rating을 그대로 감싼다 — 그 함수가
    이미 둘째 자리에서 버린다(1/10000 단위 정수를 //100으로 두 자리 버린 뒤 /100).
    975,000점 미만 구간(900k/800k/500k 보간, 500k 미만은 0)도 같은 함수 안에 있다
    (사용자 확인: vendored 실제 공식 사용, 재구현하지 않는다).

    둘째 자리에서 버리는 것이 맞다 — 넷째 자리까지 들고 있다가 합산 뒤에만 버리면
    안 된다. 실제 저장본(베스트30+신곡20+프로필, 같은 로그인 시점)으로 검산했을 때
    곡마다 둘째 자리 버림 후 합산해야 표시 레이팅과 맞았고, 넷째 자리를 유지하면
    어긋났다. docs/design/skills/chunithm-report.md도 이 정밀도로 고쳤다."""
    if const is None:
        return None
    return calculate_rating(score, const)


def average_rating(ratings: Sequence[Decimal | None], *, places: int = 4) -> Decimal | None:
    """베스트30 평균, 신곡20 평균, 전체 레이팅(50으로 나눔)에 공통으로 쓴다. 입력은 이미
    chart_rating()으로 둘째 자리까지 버려진 값들이다.

    넷째 자리에서 버린다(사용자 확인 2026-10-18). 전체 레이팅(분모 5000 = 2³×5⁴)과
    신곡20 평균(분모 2000 = 2⁴×5³)은 넷째 자리에서 정확히 끝난다. 베스트30 평균(분모
    3000 = 2³×3×5³)은 3이 섞여 애초에 유한소수로 안 끝나지만, 세 지표를 같은 자리수로
    맞추기 위해 똑같이 넷째 자리에서 버린다.

    하나라도 "계산 불가"(None)면 평균도 None이다 — 일부만 계산해 그럴듯한 숫자를
    내지 않는다."""
    if not ratings:
        return Decimal(0).quantize(Decimal(1).scaleb(-places))
    known = [r for r in ratings if r is not None]
    if len(known) != len(ratings):
        return None
    total = sum(known, Decimal(0))
    return (total / len(ratings)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)


def overall_rating(
    best30: Sequence[Decimal | None], new20: Sequence[Decimal | None], *, best_size: int = 30, new_size: int = 20
) -> Decimal | None:
    """전체 레이팅 — 베스트30·신곡20의 곡 레이팅 합을 50으로 나눈다. 프레임이 덜 찼으면(버전이 바뀌어
    신곡이 아직 없을 때 등) 빈 칸은 0으로 센다. 그래서 신곡20이 비면 레이팅이 크게 떨어진다. 하나라도
    "계산 불가"(None)면 None이다."""
    padded = [
        *best30, *[Decimal(0)] * max(0, best_size - len(best30)),
        *new20, *[Decimal(0)] * max(0, new_size - len(new20)),
    ]
    return average_rating(padded)


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


def match_session_for_chart(
    chart_key: tuple[str, str],
    recent_by_chart: dict[tuple[str, str], list[datetime]],
    sessions_by_date: dict[date, str],
    latest_session_id: str | None,
) -> tuple[str | None, bool]:
    """그 채보의 최근 플레이 이력으로 세션을 매칭한다. `recent_by_chart`에 그
    채보가 있으면 가장 늦은(max) `achieved_at`의 날짜로 `sessions_by_date`를
    찾아 (session_id, False)를 돌려준다. 없거나(또는 그 날짜가
    `sessions_by_date`에 없어 데이터가 어긋나면) 가장 최근 세션
    `latest_session_id`로 추정 매칭해 (latest_session_id, True)를 돌려준다.
    `latest_session_id`도 없으면 (None, True) — 호출하는 쪽이 오늘 날짜 세션을
    새로 만들어 다시 불러야 한다."""
    achieved_ats = recent_by_chart.get(chart_key)
    if achieved_ats:
        latest_date = max(achieved_ats).date()
        session_id = sessions_by_date.get(latest_date)
        if session_id is not None:
            return session_id, False
    return latest_session_id, True


def frame_changes(
    previous: set[tuple[str, str]], current: set[tuple[str, str]]
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """프레임(베스트30/신곡20 등) 진입과 이탈 집합. (진입, 이탈) =
    (`current - previous`, `previous - current`)."""
    return current - previous, previous - current


def candidate_upside(
    const: float, frame_floor: Decimal, *, target_bonus: Decimal = Decimal("2.00")
) -> Decimal:
    """후보곡(현재 프레임 밖)이 SSS(target_bonus 기본 2.00)를 냈을 때
    프레임 최저점을 얼마나 넘을지. `Decimal(str(const)) + target_bonus -
    frame_floor`를 그대로 돌려준다 — float 오차를 피하려 const를 문자열로 거쳐
    Decimal로 바꾼다. 음수면 그대로 음수로 돌려준다(아직 프레임에 못 들어온다는
    뜻이고, 0 이하를 거르는 것은 호출하는 쪽의 몫이다). 자리수를 버리거나
    반올림하지 않는다 — 필요하면 호출하는 쪽이 정리한다."""
    return Decimal(str(const)) + target_bonus - frame_floor
