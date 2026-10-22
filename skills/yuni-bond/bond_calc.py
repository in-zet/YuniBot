# skills/yuni-bond/bond_calc.py
"""이정표의 순수 계산. 날짜·기간·경계는 전부 여기서 코드가 정한다(LLM에 맡기지 않는다).
레이팅 단계·별·층의 경계는 `services/chunithm/rating_tier.py`가 정한다."""
from __future__ import annotations

from datetime import date
from decimal import ROUND_CEILING, Decimal

# 상수별 1호로 남기는 달성, 낮은 쪽부터. 랭크는 SSS(트리플)·SSS+(트플플), 램프는 FC(풀콤)·AJ(올저). 높은 쪽이 낮은 쪽을 포함한다
# (SSS+ 기록은 SSS도 만족하고, AJ 램프는 FC도 만족한다). AJC는 따로 세지 않고 AJ에 든다(2026-10-22 결정).
RANK_TIERS: tuple[str, ...] = ("SSS", "SSS+")
LAMP_TIERS: tuple[str, ...] = ("FC", "AJ")
FIRST_TIERS: tuple[str, ...] = (*RANK_TIERS, *LAMP_TIERS)
TIER_LABELS = {"SSS": "트리플", "SSS+": "트플플", "FC": "풀콤", "AJ": "올저"}
# records_pb의 콤보 램프 글자 → 높을수록 좋은 순서.
_COMBO_ORDER = {"NONE": 0, "FULL COMBO": 1, "ALL JUSTICE": 2, "AJC": 3}
_LAMP_NEEDS = {"FC": 1, "AJ": 2}
# 같은 채보의 기록 갱신이 이만큼 쌓이면 '오래 붙잡은 곡'이다.
LONG_HAUL_UPDATES = 5
# 약속은 이 일수가 지나면 닫는다.
PROMISE_EXPIRE_DAYS = 30
# 약속 한 줄의 최대 글자 수와, 프롬프트 맥락에 넣는 약속의 최대 개수.
PROMISE_MAX_CHARS = 80
PROMISE_PROMPT_LIMIT = 3
# 오랜만의 동기화 기준(일)의 기본값.
DEFAULT_AWAY_DAYS = 14


def remaining_to(boundary: Decimal, current: Decimal) -> Decimal:
    """경계까지 남은 레이팅. 모자라게 말하지 않도록 넷째 자리에서 올린다."""
    return (boundary - current).quantize(Decimal("0.0001"), rounding=ROUND_CEILING)


def rank_tiers_reached(old_rank: str | None, new_rank: str) -> list[str]:
    """이번 갱신으로 처음 넘은 랭크(낮은 쪽부터). 이미 그 이상이었으면 빈다."""
    order = {tier: index for index, tier in enumerate(RANK_TIERS, start=1)}
    before = order.get(old_rank or "", 0)
    after = order.get(new_rank, 0)
    return [tier for tier in RANK_TIERS if before < order[tier] <= after]


def lamp_tiers_reached(old_lamp: str | None, new_lamp: str | None) -> list[str]:
    """이번에 처음 넘은 콤보 램프 단계(FC, AJ). 램프 글자는 records_pb 값이다."""
    before = _COMBO_ORDER.get(old_lamp or "", 0)
    after = _COMBO_ORDER.get(new_lamp or "", 0)
    return [tier for tier in LAMP_TIERS if before < _LAMP_NEEDS[tier] <= after]


def holds_tier(rank: str | None, combo_lamp: str | None, tier: str) -> bool:
    """이 랭크·램프가 그 달성(SSS·SSS+·FC·AJ)을 만족하나."""
    if tier in RANK_TIERS:
        order = {name: index for index, name in enumerate(RANK_TIERS, start=1)}
        return order.get(rank or "", 0) >= order[tier]
    return _COMBO_ORDER.get(combo_lamp or "", 0) >= _LAMP_NEEDS[tier]


def const_text(const: float) -> str:
    """상수 글자(항상 소수 첫째 자리까지, 15.0)."""
    return f"{const:.1f}"


def days_between(start_iso: str, end_iso: str) -> int:
    """두 ISO 날짜 사이의 일수. 거꾸로면 0."""
    return max(0, (date.fromisoformat(end_iso[:10]) - date.fromisoformat(start_iso[:10])).days)


def date_label(iso: str, *, today: date) -> str:
    """ISO 날짜를 말로 옮긴다. 올해가 아니면 연도를 붙인다. 모델이 이 문자열의 숫자만 말하게 한다."""
    day = date.fromisoformat(iso[:10])
    text = f"{day.month}월 {day.day}일"
    return text if day.year == today.year else f"{day.year}년 {text}"


def clean_promise(text: str) -> str:
    """약속 한 줄: 줄바꿈을 공백으로 접고 길이를 자른다."""
    return " ".join(text.split())[:PROMISE_MAX_CHARS]
