# core/gemini_retry.py
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from google.genai import errors as genai_errors
from google.genai import types

from core.llm import OnRetry

logger = logging.getLogger(__name__)

# Gemini가 일시 과부하(503)·속도 제한(429)일 때만 재시도한다 — 나머지 4xx(잘못된 요청,
# 인증 등)는 다시 불러도 똑같이 실패하니 바로 올린다. 2026-10-18 실기에서 재시도 없이
# 한 번 실패하면 바로 고정 문구로 답하는 문제를 확인했다(docs/plan.md §5). core/llm_gemini.py의
# GeminiLlmAdapter.generate와 core/search_gemini.py의 GeminiSearchAdapter.search가 같이 쓴다
# (복사 금지 — 두 어댑터 모두 같은 google-genai 클라이언트를 같은 이유로 재시도해야 한다).
_RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 3
# 504(DEADLINE_EXCEEDED)는 요청 타임아웃에 걸렸다는 뜻이다. 모델이 응답 없이 멈춘 것이라(2026-10-19
# gemini-3.8-flash가 25초 넘게 무응답) 다시 불러도 같은 시간을 또 기다릴 가능성이 커서 한 번만 재시도한다.
_MAX_RETRIES_BY_CODE = {504: 1}
# 보조 모델이 있으면 504는 같은 모델로 재시도하지 않고 바로 보조 모델로 넘긴다(2026-10-20).
DEADLINE_EXCEEDED_CODES = frozenset({504})
_RETRY_BASE_DELAY_SECONDS = 2.0  # 시도마다 2배로 늘린다(2초, 4초, 8초).

# 요청 타임아웃. 없으면 모델이 응답 없이 멈출 때 멘션 처리가 끝없이 기다린다. 서버가 10초 미만
# 마감은 400으로 거절하고, 초과하면 504로 끊어 준다(위 재시도 경로로 들어간다).
LLM_REQUEST_TIMEOUT_SECONDS = 20
SEARCH_REQUEST_TIMEOUT_SECONDS = 60  # Google 검색을 거치는 호출이라 더 오래 걸린다.


def http_options(timeout_seconds: int) -> types.HttpOptions:
    """genai.Client(http_options=...)에 넘길 요청 타임아웃. 두 Gemini 어댑터가 같이 쓴다."""
    return types.HttpOptions(timeout=timeout_seconds * 1000)


def is_retryable(exc: genai_errors.APIError) -> bool:
    """일시 오류(과부하·속도 제한·타임아웃)인지. 보조 모델로 넘길지 판단할 때도 같은 기준을 쓴다."""
    return exc.code in _RETRYABLE_CODES


async def _timeout_as_504[T](call: Callable[[], Awaitable[T]]) -> T:
    """클라이언트 쪽 타이머가 서버의 504보다 먼저 끊으면 날것 `TimeoutError`가 올라와 재시도·보조 모델 전환을
    건너뛴다(2026-10-21, 데드라인을 20초로 줄인 뒤 확인). 같은 뜻의 504로 바꿔 같은 경로를 타게 한다."""
    try:
        return await call()
    except TimeoutError as exc:
        raise genai_errors.APIError(
            504, {"error": {"message": "클라이언트 요청 타임아웃", "status": "DEADLINE_EXCEEDED"}}
        ) from exc


async def call_with_retry[T](
    call: Callable[[], Awaitable[T]],
    *,
    on_retry: OnRetry | None = None,
    skip_retry_codes: frozenset[int] = frozenset(),
) -> T:
    """`skip_retry_codes`에 든 오류 코드는 재시도 없이 바로 올린다 — 보조 모델이 있을 때 504를 같은
    모델로 또 기다리지 않고 바로 넘기려고 쓴다."""
    attempt = 0
    while True:
        try:
            return await _timeout_as_504(call)
        except genai_errors.APIError as exc:
            attempt += 1
            if (
                exc.code not in _RETRYABLE_CODES
                or exc.code in skip_retry_codes
                or attempt > _MAX_RETRIES_BY_CODE.get(exc.code, _MAX_RETRIES)
            ):
                raise
            delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Gemini %s, 재시도 %d/%d (%.0f초 후): %s", exc.code, attempt, _MAX_RETRIES, delay, exc.message,
            )
            if on_retry is not None:
                await on_retry(attempt, delay)
            await asyncio.sleep(delay)
