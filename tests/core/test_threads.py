# tests/core/test_threads.py
from datetime import UTC, datetime, timedelta

import pytest

from core.threads import ThreadManager, ThreadTranscript


class _FakeTransport:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, text: str) -> None:
        self.sent.append(text)


@pytest.mark.asyncio
async def test_receive_appends_transcript_and_resets_idle_timer() -> None:
    manager = ThreadManager()
    transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = await manager.open(
        skill_name="chunithm-session",
        player_id="p1",
        transport=transport,
        idle_timeout=300.0,
        close_timeout=300.0,
        on_close=lambda t: closed.append(t),
        now=base,
    )

    await manager.receive(session.thread_id, "p1", "오늘 세션 어땠어", now=base + timedelta(seconds=10))
    assert session.transcript[0].content == "오늘 세션 어땠어"

    # 10초 뒤에도 idle_timeout(300초)에 한참 못 미치므로 체크해도 아직 살아 있다
    await manager.check_idle(now=base + timedelta(seconds=20))
    assert transport.sent == []
    assert closed == []


@pytest.mark.asyncio
async def test_idle_timeout_asks_then_closes() -> None:
    manager = ThreadManager()
    transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    _ = await manager.open(
        skill_name="chunithm-session",
        player_id="p1",
        transport=transport,
        idle_timeout=300.0,
        close_timeout=300.0,
        on_close=lambda t: closed.append(t),
        now=base,
    )

    await manager.check_idle(now=base + timedelta(seconds=301))
    assert len(transport.sent) == 1  # 무응답 확인 메시지

    await manager.check_idle(now=base + timedelta(seconds=301 + 301))
    assert len(closed) == 1
    assert closed[0].closed_reason == "timeout"


@pytest.mark.asyncio
async def test_message_during_check_phase_cancels_close() -> None:
    manager = ThreadManager()
    transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = await manager.open(
        skill_name="chunithm-session",
        player_id="p1",
        transport=transport,
        idle_timeout=300.0,
        close_timeout=300.0,
        on_close=lambda t: closed.append(t),
        now=base,
    )

    await manager.check_idle(now=base + timedelta(seconds=301))
    await manager.receive(session.thread_id, "p1", "아직 안 끝났어", now=base + timedelta(seconds=310))
    await manager.check_idle(now=base + timedelta(seconds=320))

    assert closed == []


@pytest.mark.asyncio
async def test_explicit_close_delivers_transcript_immediately() -> None:
    manager = ThreadManager()
    transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = await manager.open(
        skill_name="chunithm-session",
        player_id="p1",
        transport=transport,
        on_close=lambda t: closed.append(t),
        now=base,
    )
    await manager.receive(session.thread_id, "p1", "끝낼게", now=base)
    await manager.close(session.thread_id, reason="explicit")

    assert len(closed) == 1
    assert closed[0].closed_reason == "explicit"
    assert closed[0].messages[0].content == "끝낼게"
