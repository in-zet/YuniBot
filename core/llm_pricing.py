from __future__ import annotations

# 100만 토큰당 USD. ai.google.dev/gemini-api/docs/pricing 2026-10-12 확인(페이지 마지막 수정
# 2026-10-09). 3.8 Flash는 2026-12-31까지 할인가이고 이후 두 배로 오른다 — 00-core.md
# "비용과 운영"의 지침대로 2027-01-01에 이 표를 다시 확인해야 한다.
_PRICING_PER_MILLION_USD: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}


def estimate_cost_usd(model: str, *, input_tokens: int, output_tokens: int) -> float:
    """요금표에 없는 모델이면 0.0을 돌려준다 — 추정 비용 계산 실패가 사용량 기록 자체를 막으면 안 된다."""
    rates = _PRICING_PER_MILLION_USD.get(model)
    if rates is None:
        return 0.0
    input_rate, output_rate = rates
    return (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate
