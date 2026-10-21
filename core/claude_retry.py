# core/claude_retry.py
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import anthropic

from core.llm import OnRetry

logger = logging.getLogger(__name__)

# Anthropic이 일시 과부하(529)·서버 오류(5xx)·속도 제한(429)이거나 연결·타임아웃에 걸렸을 때만 재시도한다.
# 나머지 4xx(잘못된 요청, 인증 등)는 다시 불러도 똑같이 실패하니 바로 올린다. SDK의 자체 재시도는 끄고
# (클라이언트를 max_retries=0으로 만든다) 여기서 한 곳으로 모은다 — 그래야 `on_retry`로 "잠깐만 기다려
# 달라" 안내를 낼 수 있다. core/llm_claude.py의 ClaudeLlmAdapter와 core/search_claude.py의
# ClaudeSearchAdapter가 같이 쓴다(복사 금지, core/gemini_retry.py와 같은 구조).
_MAX_RETRIES = 3
# 타임아웃은 모델이 응답 없이 멈춘 것이라 다시 불러도 같은 시간을 또 기다릴 가능성이 커서 한 번만 재시도한다.
_MAX_RETRIES_ON_TIMEOUT = 1
_RETRY_BASE_DELAY_SECONDS = 2.0  # 시도마다 2배로 늘린다(2초, 4초, 8초).

# 요청 타임아웃. 없으면 SDK 기본값이 10분이라 모델이 멈출 때 멘션 처리와 플레이어 턴 잠금이 끝없이 잡힌다.
LLM_REQUEST_TIMEOUT_SECONDS = 30.0
SEARCH_REQUEST_TIMEOUT_SECONDS = 60.0  # 웹 검색을 거치는 호출이라 더 오래 걸린다.
# 검색은 한 번 기다림이 길어서 재시도를 한 번만 한다(Gemini 쪽과 같은 이유, core/gemini_retry.py).
SEARCH_MAX_RETRIES = 1


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, anthropic.APITimeoutError | anthropic.APIConnectionError | anthropic.RateLimitError):
        return True
    return isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500


async def call_with_retry[T](
    call: Callable[[], Awaitable[T]], *, on_retry: OnRetry | None = None, max_retries: int = _MAX_RETRIES
) -> T:
    attempt = 0
    while True:
        try:
            return await call()
        except (anthropic.APIError, TimeoutError) as exc:
            attempt += 1
            timed_out = isinstance(exc, anthropic.APITimeoutError | TimeoutError)
            limit = min(max_retries, _MAX_RETRIES_ON_TIMEOUT) if timed_out else max_retries
            if (not isinstance(exc, TimeoutError) and not is_retryable(exc)) or attempt > limit:
                raise
            delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning("Claude %s, 재시도 %d/%d (%.0f초 후): %s", type(exc).__name__, attempt, limit, delay, exc)
            if on_retry is not None:
                await on_retry(attempt, delay)
            await asyncio.sleep(delay)
