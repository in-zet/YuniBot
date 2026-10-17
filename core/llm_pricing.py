from __future__ import annotations

# 100만 토큰당 USD. ai.google.dev/gemini-api/docs/pricing 2026-10-12 확인(페이지 마지막 수정
# 2026-10-09). 3.8 Flash는 2026-12-31까지 할인가이고 이후 두 배로 오른다 — 00-core.md
# "비용과 운영"의 지침대로 2027-01-01에 이 표를 다시 확인해야 한다.
_PRICING_PER_MILLION_USD: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    # 2026-10-17 확인(공식 가격표 페이지는 못 찾아 apidog.com/VKTR 등 제3자 발췌로 확인).
    # 프롬프트 100K 토큰 이하 표준가. 100K를 넘으면 $0.50/$2.50로 올라가는데, 이 봇의 멘션
    # 하나가 그 크기에 이를 일은 없다고 보고 낮은 쪽만 쓴다.
    "claude-haiku-5-5": (0.10, 0.50),
}


def estimate_cost_usd(model: str, *, input_tokens: int, output_tokens: int) -> float:
    """요금표에 없는 모델이면 0.0을 돌려준다 — 추정 비용 계산 실패가 사용량 기록 자체를 막으면 안 된다."""
    rates = _PRICING_PER_MILLION_USD.get(model)
    if rates is None:
        return 0.0
    input_rate, output_rate = rates
    return (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate
