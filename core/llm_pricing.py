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


# Claude web_search 서버 도구는 토큰값과 별개로 검색 1,000회당 10달러다(2026-10-21 Claude API 문서 확인).
# 호출마다 `usage.server_tool_use.web_search_requests`로 센다.
WEB_SEARCH_FEE_USD = 10.0 / 1000


# 캐시에서 읽힌 입력 토큰은 일반 입력의 이 비율로 계산한다. Gemini·Claude 모두 캐시 읽기를 입력 가격의
# 10% 안팎으로 받는다고 보고 둔 가정이다(가격표에 캐시 단가를 따로 넣기 전까지 — 2026-10-21).
CACHED_INPUT_RATIO = 0.1


def billable_input_tokens(input_tokens: int, cached_input_tokens: int = 0) -> int:
    """입력 토큰 중 캐시에서 읽힌 몫을 `CACHED_INPUT_RATIO`만 치고 센 값. 토큰 상한과 비용 추정이 같이 쓴다."""
    cached = min(max(cached_input_tokens, 0), input_tokens)
    return input_tokens - cached + round(cached * CACHED_INPUT_RATIO)


def estimate_cost_usd(
    model: str, *, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0
) -> float:
    """요금표에 없는 모델이면 0.0을 돌려준다 — 추정 비용 계산 실패가 사용량 기록 자체를 막으면 안 된다.
    `input_tokens`는 캐시에서 읽힌 몫(`cached_input_tokens`)을 포함한 전체 입력이다."""
    rates = _PRICING_PER_MILLION_USD.get(model)
    if rates is None:
        return 0.0
    input_rate, output_rate = rates
    billable = billable_input_tokens(input_tokens, cached_input_tokens)
    return (billable / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate


def estimate_search_fee_usd(search_requests: int) -> float:
    return max(search_requests, 0) * WEB_SEARCH_FEE_USD
