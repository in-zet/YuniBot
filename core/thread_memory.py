# core/thread_memory.py
from __future__ import annotations

import logging
from typing import Protocol

from core.threads import ThreadTranscript
from services.common.memory import PendingMessage, strip_mentions

logger = logging.getLogger(__name__)


class _MemoryLike(Protocol):
    async def enqueue(
        self,
        player_id: str,
        *,
        messages: list[PendingMessage],
        guild_id: str | None,
        channel_id: str | None,
        message_id: str | None,
        now: float,
    ) -> bool: ...


async def queue_thread_memory(memory: _MemoryLike, transcript: ThreadTranscript, *, now: float) -> None:
    """닫힌 대화 스레드에서 본인이 쓴 글을 메모리 요약 대기 목록에 쌓는다(멘션 응답과 같은 경로).
    메시지 id가 없는 글은 스레드 id와 순번으로 구분한다. 실패해도 스레드를 닫는 흐름은 막지 않는다."""
    messages = [
        PendingMessage(id=m.message_id or f"{transcript.thread_id}:{index}", text=strip_mentions(m.content))
        for index, m in enumerate(transcript.messages)
    ]
    last_message_id = next((m.message_id for m in reversed(transcript.messages) if m.message_id), None)
    try:
        _ = await memory.enqueue(
            transcript.player_id, messages=messages, guild_id=None, channel_id=transcript.channel_id,
            message_id=last_message_id, now=now,
        )
    except Exception:
        logger.warning("스레드 %s의 메모리 요약 대기 등록 실패", transcript.thread_id, exc_info=True)
