# tests/core/test_thread_memory.py
from datetime import UTC, datetime
from typing import Any

from core.thread_memory import queue_thread_memory
from core.threads import ThreadMessage, ThreadTranscript
from services.common.memory import PendingMessage


class _FakeMemory:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail

    async def enqueue(
        self, player_id: str, *, messages: list[PendingMessage], guild_id: str | None, channel_id: str | None,
        message_id: str | None, now: float,
    ) -> bool:
        if self._fail:
            raise RuntimeError("db down")
        self.calls.append(
            {"player_id": player_id, "messages": messages, "channel_id": channel_id, "message_id": message_id, "now": now}
        )
        return True


def _transcript(messages: list[ThreadMessage]) -> ThreadTranscript:
    return ThreadTranscript(
        thread_id="t1", player_id="p1", messages=messages, closed_reason="timeout",
        skill_name="session", channel_id="c1",
    )


async def test_transcript_goes_to_the_pending_queue_with_mentions_stripped() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    memory = _FakeMemory()
    transcript = _transcript(
        [
            ThreadMessage(player_id="p1", content="<@123> 오늘 크로노 연습했어", at=at, message_id="m1"),
            ThreadMessage(player_id="p1", content="내일도 갈 거야", at=at),
        ]
    )

    await queue_thread_memory(memory, transcript, now=1000.0)

    call = memory.calls[0]
    assert call["player_id"] == "p1"
    assert [(m.id, m.text) for m in call["messages"]] == [("m1", "오늘 크로노 연습했어"), ("t1:1", "내일도 갈 거야")]
    # 📝 반응은 마지막으로 메시지 id가 있는 글에 단다.
    assert (call["channel_id"], call["message_id"], call["now"]) == ("c1", "m1", 1000.0)


async def test_queue_failure_does_not_raise() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    transcript = _transcript([ThreadMessage(player_id="p1", content="안녕", at=at)])

    await queue_thread_memory(_FakeMemory(fail=True), transcript, now=1.0)


async def test_yuni_lines_are_not_queued_as_the_players_writing() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    memory = _FakeMemory()
    transcript = _transcript(
        [
            ThreadMessage(player_id="p1", content="어땠어?", at=at, is_yuni=True),
            ThreadMessage(player_id="p1", content="좋았어", at=at, message_id="m1"),
        ]
    )

    await queue_thread_memory(memory, transcript, now=1.0)

    assert [m.text for m in memory.calls[0]["messages"]] == ["좋았어"]
