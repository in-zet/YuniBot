# core/threads.py
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

ClosedReason = Literal["explicit", "timeout", "shutdown"]


class ThreadTransport(Protocol):
    async def send_message(self, text: str) -> None: ...


@dataclass(slots=True, kw_only=True)
class ThreadMessage:
    player_id: str
    content: str
    at: datetime
    # 디스코드 메시지 id. 메모리 요약 대기 목록의 중복 제거와 📝 반응에 쓴다.
    message_id: str | None = None
    # 유니가 한 말이면 True(player_id는 이 대화의 플레이어 id를 그대로 둔다). 전문 저장에는 들어가고
    # 메모리 요약 대기 목록에는 들어가지 않는다.
    is_yuni: bool = False


@dataclass(slots=True, kw_only=True)
class ThreadTranscript:
    thread_id: str
    player_id: str
    messages: list[ThreadMessage]
    closed_reason: ClosedReason
    skill_name: str = ""
    channel_id: str | None = None


@dataclass(slots=True, kw_only=True)
class ThreadSession:
    thread_id: str
    skill_name: str
    player_id: str
    transport: ThreadTransport
    # 스레드(또는 DM)의 디스코드 채널 id. 들어온 메시지가 이 대화의 것인지 가리는 열쇠다.
    channel_id: str
    idle_timeout: float
    close_timeout: float
    on_close: Callable[[ThreadTranscript], None | Awaitable[None]]
    transcript: list[ThreadMessage] = field(default_factory=list)
    last_activity: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))
    state: Literal["open", "checking", "closed"] = "open"
    checked_at: datetime | None = None
    # 이 대화에서 펼쳐 둔 스킬(오래 안 쓴 순). 메시지마다 이어받는다.
    opened_skills: list[str] = field(default_factory=list)
    # 이 대화 안에서만 프롬프트 맥락에 넣는 지침(연 스킬이 정한다. 예: 후기용 말투와 리포트 요약).
    guidance: str = ""

    def record_yuni(self, content: str, *, at: datetime) -> None:
        """유니가 한 말을 전문에 남긴다. 무응답 시간은 플레이어의 마지막 말부터 재므로 last_activity는 건드리지 않는다."""
        self.transcript.append(ThreadMessage(player_id=self.player_id, content=content, at=at, is_yuni=True))


@dataclass(slots=True)
class ThreadEndRequest:
    """한 번의 응답 동안 모델이 대화를 끝내자고 했는지(core_tools의 end_conversation이 켠다)."""

    requested: bool = False


OnClosed = Callable[[ThreadTranscript], Awaitable[None]]


class ThreadManager:
    def __init__(self, *, on_closed: OnClosed | None = None) -> None:
        self._sessions: dict[str, ThreadSession] = {}
        # 어느 스킬이 연 스레드든 닫힐 때 코어가 한 번 부르는 처리(메모리 요약 등). 연 스킬의 on_close보다 먼저 돈다.
        self._on_closed = on_closed

    async def open(
        self,
        *,
        skill_name: str,
        player_id: str,
        transport: ThreadTransport,
        channel_id: str,
        on_close: Callable[[ThreadTranscript], None | Awaitable[None]],
        idle_timeout: float = 300.0,
        close_timeout: float = 300.0,
        guidance: str = "",
        now: datetime,
    ) -> ThreadSession:
        session = ThreadSession(
            thread_id=str(uuid.uuid4()),
            skill_name=skill_name,
            player_id=player_id,
            transport=transport,
            channel_id=channel_id,
            idle_timeout=idle_timeout,
            close_timeout=close_timeout,
            on_close=on_close,
            guidance=guidance,
            last_activity=now,
        )
        self._sessions[session.thread_id] = session
        return session

    def discard(self, thread_id: str) -> None:
        """열다가 실패한 대화를 훅 없이 버린다(첫 메시지를 못 보냈을 때)."""
        _ = self._sessions.pop(thread_id, None)

    def open_for(self, player_id: str, skill_name: str) -> ThreadSession | None:
        """이 플레이어가 이 스킬로 이미 열어 둔 대화(닫히지 않은 것)."""
        for session in self._sessions.values():
            if session.player_id == player_id and session.skill_name == skill_name and session.state != "closed":
                return session
        return None

    def session_for_channel(self, channel_id: str) -> ThreadSession | None:
        """이 채널에서 아직 닫히지 않은 대화를 찾는다."""
        for session in self._sessions.values():
            if session.channel_id == channel_id and session.state != "closed":
                return session
        return None

    async def receive(
        self, thread_id: str, player_id: str, content: str, *, now: datetime, message_id: str | None = None
    ) -> None:
        session = self._sessions.get(thread_id)
        if session is None or session.state == "closed":
            return
        if player_id != session.player_id:
            return
        session.transcript.append(ThreadMessage(player_id=player_id, content=content, at=now, message_id=message_id))
        session.last_activity = now
        session.state = "open"
        session.checked_at = None

    async def check_idle(self, *, now: datetime) -> None:
        for session in list(self._sessions.values()):
            try:
                await self._check_session(session, now=now)
            except Exception:
                logger.exception("thread check failed for %s (%s)", session.thread_id, session.skill_name)

    async def _check_session(self, session: ThreadSession, *, now: datetime) -> None:
        if session.state == "closed":
            return

        if session.state == "open":
            if (now - session.last_activity).total_seconds() >= session.idle_timeout:
                await session.transport.send_message("다 끝났나요?")
                session.state = "checking"
                session.checked_at = now
        elif session.state == "checking":
            assert session.checked_at is not None
            if (now - session.checked_at).total_seconds() >= session.close_timeout:
                await self.close(session.thread_id, reason="timeout")

    async def close(self, thread_id: str, *, reason: ClosedReason) -> None:
        session = self._sessions.get(thread_id)
        if session is None or session.state == "closed":
            return
        session.state = "closed"
        transcript = ThreadTranscript(
            thread_id=session.thread_id,
            player_id=session.player_id,
            messages=list(session.transcript),
            closed_reason=reason,
            skill_name=session.skill_name,
            channel_id=session.channel_id,
        )
        del self._sessions[thread_id]
        if self._on_closed is not None:
            try:
                await self._on_closed(transcript)
            except Exception:
                logger.exception("thread close hook failed for %s", thread_id)
        result = session.on_close(transcript)
        if result is not None:
            await result

    async def close_all(self, *, reason: ClosedReason) -> None:
        for thread_id in list(self._sessions):
            try:
                await self.close(thread_id, reason=reason)
            except Exception:
                logger.exception("thread close failed for %s", thread_id)
