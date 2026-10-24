# services/maimai/rating.py
"""maimai DX 곡 레이팅. 달성률 계수표는 TrueRou/maimai.py의 `utils/coefficient.py`(MIT, 원 출처
Diving-Fish/maimaidx-prober)를 옮겨 적었다. 국제판 NET 표시 레이팅과의 대조는 아직 안 했다(§17.10)."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

# 달성률은 ×10000 정수로 다룬다(100.5000% = 1_005_000). (이 달성률 이상, 계수) 오름차순.
_COEFFICIENTS: tuple[tuple[int, str], ...] = (
    (0, "0"), (100_000, "1.6"), (200_000, "3.2"), (300_000, "4.8"), (400_000, "6.4"),
    (500_000, "8.0"), (600_000, "9.6"), (700_000, "11.2"), (750_000, "12.0"), (799_999, "12.8"),
    (800_000, "13.6"), (900_000, "15.2"), (940_000, "16.8"), (969_999, "17.6"),
    (970_000, "20.0"), (980_000, "20.3"), (989_999, "20.6"),
    (990_000, "20.8"), (995_000, "21.1"), (999_999, "21.4"),
    (1_000_000, "21.6"), (1_004_999, "22.2"), (1_005_000, "22.4"),
)
_CAP = 1_005_000  # 달성률 100.5%를 넘는 부분은 레이팅에 더하지 않는다
_DENOMINATOR = Decimal(1_000_000)  # 달성률 ×10000 정수 → 비율(÷10000)에 다시 ÷100


def coefficient(achievement: int) -> Decimal:
    chosen = _COEFFICIENTS[0][1]
    for floor, value in _COEFFICIENTS:
        if achievement < floor:
            break
        chosen = value
    return Decimal(chosen)


def chart_rating(achievement: int, const: float | None) -> Decimal | None:
    """채보 하나의 레이팅(정수로 버림). 상수를 모르면 None("계산 불가") — 모르는 값을 0으로 쓰지 않는다.
    float 오차를 피하려 계수·상수를 문자열을 거쳐 Decimal로 계산한다."""
    if const is None:
        return None
    value = coefficient(achievement) * Decimal(str(const)) * min(achievement, _CAP) / _DENOMINATOR
    return value.quantize(Decimal(1), rounding=ROUND_DOWN)
