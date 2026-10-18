# core/gemini_retry.py
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from google.genai import errors as genai_errors

from core.llm import OnRetry

logger = logging.getLogger(__name__)

# Gemini가 일시 과부하(503)·속도 제한(429)일 때만 재시도한다 — 나머지 4xx(잘못된 요청,
# 인증 등)는 다시 불러도 똑같이 실패하니 바로 올린다. 2026-10-18 실기에서 재시도 없이
# 한 번 실패하면 바로 고정 문구로 답하는 문제를 확인했다(docs/plan.md §5). core/llm_gemini.py의
# GeminiLlmAdapter.generate와 core/search_gemini.py의 GeminiSearchAdapter.search가 같이 쓴다
# (복사 금지 — 두 어댑터 모두 같은 google-genai 클라이언트를 같은 이유로 재시도해야 한다).
_RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 3
_RETRY_BASE_DELAY_SECONDS = 2.0  # 시도마다 2배로 늘린다(2초, 4초, 8초).


async def call_with_retry[T](call: Callable[[], Awaitable[T]], *, on_retry: OnRetry | None = None) -> T:
    attempt = 0
    while True:
        try:
            return await call()
        except genai_errors.APIError as exc:
            attempt += 1
            if exc.code not in _RETRYABLE_CODES or attempt > _MAX_RETRIES:
                raise
            delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Gemini %s, 재시도 %d/%d (%.0f초 후): %s", exc.code, attempt, _MAX_RETRIES, delay, exc.message,
            )
            if on_retry is not None:
                await on_retry(attempt, delay)
            await asyncio.sleep(delay)
