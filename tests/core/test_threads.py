# tests/core/test_threads.py
from datetime import UTC, datetime, timedelta

import pytest

from core.fallback_messages import IDLE_CHECK_MESSAGES
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
        skill_name="session",
        player_id="p1",
        transport=transport,
        channel_id="c1",
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
        skill_name="session",
        player_id="p1",
        transport=transport,
        channel_id="c1",
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
        skill_name="session",
        player_id="p1",
        transport=transport,
        channel_id="c1",
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
        skill_name="session",
        player_id="p1",
        transport=transport,
        channel_id="c1",
        on_close=lambda t: closed.append(t),
        now=base,
    )
    await manager.receive(session.thread_id, "p1", "끝낼게", now=base)
    await manager.close(session.thread_id, reason="explicit")

    assert len(closed) == 1
    assert closed[0].closed_reason == "explicit"
    assert closed[0].messages[0].content == "끝낼게"


class _RaisingTransport:
    async def send_message(self, text: str) -> None:
        _ = text
        raise RuntimeError("discord down")


@pytest.mark.asyncio
async def test_message_from_other_player_is_rejected() -> None:
    manager = ThreadManager()
    transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = await manager.open(
        skill_name="session",
        player_id="p1",
        transport=transport,
        channel_id="c1",
        idle_timeout=300.0,
        close_timeout=300.0,
        on_close=lambda t: closed.append(t),
        now=base,
    )

    await manager.receive(session.thread_id, "p2", "남의 메시지", now=base + timedelta(seconds=10))

    assert session.transcript == []
    assert session.last_activity == base
    assert session.state == "open"


@pytest.mark.asyncio
async def test_receive_on_unknown_or_closed_thread_is_noop() -> None:
    manager = ThreadManager()
    transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    await manager.receive("no-such-thread", "p1", "유령", now=base)  # 예외 없이 무시

    session = await manager.open(
        skill_name="session",
        player_id="p1",
        transport=transport,
        channel_id="c1",
        on_close=lambda t: closed.append(t),
        now=base,
    )
    await manager.close(session.thread_id, reason="explicit")
    await manager.receive(session.thread_id, "p1", "닫힌 뒤", now=base + timedelta(seconds=1))

    assert session.transcript == []
    assert len(closed) == 1


@pytest.mark.asyncio
async def test_double_close_delivers_transcript_once() -> None:
    manager = ThreadManager()
    transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = await manager.open(
        skill_name="session",
        player_id="p1",
        transport=transport,
        channel_id="c1",
        on_close=lambda t: closed.append(t),
        now=base,
    )
    await manager.close(session.thread_id, reason="explicit")
    await manager.close(session.thread_id, reason="timeout")

    assert len(closed) == 1
    assert closed[0].closed_reason == "explicit"


@pytest.mark.asyncio
async def test_failing_session_does_not_block_other_sessions() -> None:
    manager = ThreadManager()
    good_transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    _ = await manager.open(
        skill_name="session",
        player_id="p1",
        transport=_RaisingTransport(),
        channel_id="c1",
        on_close=lambda t: None,
        now=base,
    )
    good = await manager.open(
        skill_name="session",
        player_id="p2",
        transport=good_transport,
        channel_id="c2",
        on_close=lambda t: closed.append(t),
        now=base,
    )

    # 첫 세션의 send_message가 예외를 던져도 두 번째 세션의 확인 메시지는 처리된다
    await manager.check_idle(now=base + timedelta(seconds=301))
    assert len(good_transport.sent) == 1 and good_transport.sent[0] in IDLE_CHECK_MESSAGES
    assert good.state == "checking"

    # 두 번째 세션의 닫기도 첫 세션 실패에 막히지 않는다
    await manager.check_idle(now=base + timedelta(seconds=602))
    assert len(closed) == 1
    assert closed[0].closed_reason == "timeout"


@pytest.mark.asyncio
async def test_session_for_channel_finds_only_open_sessions() -> None:
    manager = ThreadManager()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = await manager.open(
        skill_name="session", player_id="p1", transport=_FakeTransport(), channel_id="c9",
        on_close=lambda t: None, now=base,
    )

    assert manager.session_for_channel("c9") is session
    assert manager.session_for_channel("other") is None

    await manager.close(session.thread_id, reason="explicit")
    assert manager.session_for_channel("c9") is None


@pytest.mark.asyncio
async def test_receive_keeps_discord_message_id_in_transcript() -> None:
    manager = ThreadManager()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    closed: list[ThreadTranscript] = []
    session = await manager.open(
        skill_name="session", player_id="p1", transport=_FakeTransport(), channel_id="c1",
        on_close=closed.append, now=base,
    )
    await manager.receive(session.thread_id, "p1", "안녕", now=base, message_id="m1")
    await manager.close(session.thread_id, reason="explicit")

    assert closed[0].messages[0].message_id == "m1"
    assert closed[0].channel_id == "c1"
    assert closed[0].skill_name == "session"


@pytest.mark.asyncio
async def test_core_close_hook_runs_before_skill_callback_and_survives_failure() -> None:
    order: list[str] = []

    async def hook(transcript: ThreadTranscript) -> None:
        order.append(f"hook:{transcript.closed_reason}")
        raise RuntimeError("요약 대기 등록 실패")

    manager = ThreadManager(on_closed=hook)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = await manager.open(
        skill_name="session", player_id="p1", transport=_FakeTransport(), channel_id="c1",
        on_close=lambda t: order.append("skill"), now=base,
    )
    await manager.close(session.thread_id, reason="explicit")

    assert order == ["hook:explicit", "skill"]


@pytest.mark.asyncio
async def test_close_all_closes_every_open_session_as_shutdown() -> None:
    manager = ThreadManager()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    closed: list[ThreadTranscript] = []
    for channel in ("c1", "c2"):
        _ = await manager.open(
            skill_name="session", player_id="p1", transport=_FakeTransport(), channel_id=channel,
            on_close=closed.append, now=base,
        )

    await manager.close_all(reason="shutdown")

    assert [t.closed_reason for t in closed] == ["shutdown", "shutdown"]
    assert manager.session_for_channel("c1") is None


async def test_open_for_finds_only_this_players_open_conversation_for_the_skill() -> None:
    manager = ThreadManager()
    session = await manager.open(
        skill_name="session", player_id="p1", transport=_FakeTransport(), channel_id="c1",
        on_close=lambda t: None, now=datetime.now(UTC),
    )

    assert manager.open_for("p1", "session") is session
    assert manager.open_for("p2", "session") is None
    assert manager.open_for("p1", "other-skill") is None

    await manager.close(session.thread_id, reason="explicit")
    assert manager.open_for("p1", "session") is None


async def test_discard_drops_the_session_without_running_hooks() -> None:
    hook_calls: list[ThreadTranscript] = []

    async def hook(transcript: ThreadTranscript) -> None:
        hook_calls.append(transcript)

    manager = ThreadManager(on_closed=hook)
    session = await manager.open(
        skill_name="s", player_id="p1", transport=_FakeTransport(), channel_id="c1",
        on_close=lambda t: None, now=datetime.now(UTC),
    )

    manager.discard(session.thread_id)

    assert manager.session_for_channel("c1") is None
    assert hook_calls == []


async def test_record_yuni_keeps_yuni_lines_in_the_transcript_without_touching_activity() -> None:
    manager = ThreadManager()
    start = datetime.now(UTC)
    session = await manager.open(
        skill_name="s", player_id="p1", transport=_FakeTransport(), channel_id="c1",
        on_close=lambda t: None, guidance="지침", now=start,
    )
    session.record_yuni("어땠어?", at=start)
    await manager.receive(session.thread_id, "p1", "좋았어", now=start + timedelta(seconds=30))
    session.record_yuni("다행이다", at=start + timedelta(seconds=31))

    assert session.guidance == "지침"
    assert [(m.content, m.is_yuni) for m in session.transcript] == [
        ("어땠어?", True), ("좋았어", False), ("다행이다", True),
    ]
    assert session.last_activity == start + timedelta(seconds=30)
