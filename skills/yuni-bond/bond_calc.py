# skills/yuni-bond/bond_calc.py
"""함께한 기록의 순수 계산. 날짜·기간·마일스톤 경계는 전부 여기서 코드가 정한다(LLM에 맡기지 않는다)."""
from __future__ import annotations

from datetime import date
from decimal import ROUND_CEILING, Decimal

# 레이팅 마일스톤은 이 간격의 경계를 넘을 때마다 하나다.
MILESTONE_STEP = Decimal("0.25")
# 랭크 첫 달성으로 남기는 랭크, 낮은 쪽부터. SSS+는 SSS를 포함한다.
RANK_TIERS: tuple[str, ...] = ("SSS", "SSS+")
# 같은 채보의 기록 갱신이 이만큼 쌓이면 '오래 붙잡은 곡'이다.
LONG_HAUL_UPDATES = 5
# 약속은 이 일수가 지나면 닫는다.
PROMISE_EXPIRE_DAYS = 30
# 약속 한 줄의 최대 글자 수와, 프롬프트 맥락에 넣는 약속의 최대 개수.
PROMISE_MAX_CHARS = 80
PROMISE_PROMPT_LIMIT = 3
# 오랜만의 동기화 기준(일)의 기본값.
DEFAULT_AWAY_DAYS = 14


def highest_milestone_crossed(previous: Decimal | None, current: Decimal | None) -> Decimal | None:
    """`previous < 경계 <= current`인 0.25 단위 경계 중 가장 높은 것. 어느 쪽이든 모르거나 오르지 않았으면 None."""
    if previous is None or current is None or current <= previous:
        return None
    boundary = (current // MILESTONE_STEP) * MILESTONE_STEP
    return boundary if boundary > previous else None


def next_milestone(current: Decimal) -> Decimal:
    return ((current // MILESTONE_STEP) + 1) * MILESTONE_STEP


def remaining_to(boundary: Decimal, current: Decimal) -> Decimal:
    """경계까지 남은 레이팅. 모자라게 말하지 않도록 넷째 자리에서 올린다."""
    return (boundary - current).quantize(Decimal("0.0001"), rounding=ROUND_CEILING)


def rank_tiers_reached(old_rank: str | None, new_rank: str) -> list[str]:
    """이번 갱신으로 처음 넘은 랭크(낮은 쪽부터). 이미 그 이상이었으면 빈다."""
    order = {tier: index for index, tier in enumerate(RANK_TIERS, start=1)}
    before = order.get(old_rank or "", 0)
    after = order.get(new_rank, 0)
    return [tier for tier in RANK_TIERS if before < order[tier] <= after]


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
