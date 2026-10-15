from core.llm_pricing import estimate_cost_usd


def test_estimate_cost_usd_matches_published_rate() -> None:
    # 100만 입력 토큰 + 100만 출력 토큰 기준, 2026-10-12 확인한 3.8 Flash 요금(할인가)
    cost = estimate_cost_usd("gemini-3.8-flash", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 0.75 + 3.75


def test_estimate_cost_usd_flash_lite() -> None:
    cost = estimate_cost_usd("gemini-3.5-flash-lite", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 0.30 + 2.50


def test_estimate_cost_usd_unknown_model_is_zero_but_does_not_raise() -> None:
    # 요금표에 없는 모델 이름이 설정값으로 들어와도 사용량 기록 전체가 죽으면 안 된다.
    assert estimate_cost_usd("some-future-model", input_tokens=1000, output_tokens=1000) == 0.0
