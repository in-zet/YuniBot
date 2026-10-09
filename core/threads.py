# core/threads.py
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

ClosedReason = Literal["explicit", "timeout"]


class ThreadTransport(Protocol):
    async def send_message(self, text: str) -> None: ...


@dataclass(slots=True, kw_only=True)
class ThreadMessage:
    player_id: str
    content: str
    at: datetime


@dataclass(slots=True, kw_only=True)
class ThreadTranscript:
    thread_id: str
    player_id: str
    messages: list[ThreadMessage]
    closed_reason: ClosedReason


@dataclass(slots=True, kw_only=True)
class ThreadSession:
    thread_id: str
    skill_name: str
    player_id: str
    transport: ThreadTransport
    idle_timeout: float
    close_timeout: float
    on_close: Callable[[ThreadTranscript], None | Awaitable[None]]
    transcript: list[ThreadMessage] = field(default_factory=list)
    last_activity: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=UTC))
    state: Literal["open", "checking", "closed"] = "open"
    checked_at: datetime | None = None


class ThreadManager:
    def __init__(self) -> None:
        self._sessions: dict[str, ThreadSession] = {}

    async def open(
        self,
        *,
        skill_name: str,
        player_id: str,
        transport: ThreadTransport,
        on_close: Callable[[ThreadTranscript], None | Awaitable[None]],
        idle_timeout: float = 300.0,
        close_timeout: float = 300.0,
        now: datetime,
    ) -> ThreadSession:
        session = ThreadSession(
            thread_id=str(uuid.uuid4()),
            skill_name=skill_name,
            player_id=player_id,
            transport=transport,
            idle_timeout=idle_timeout,
            close_timeout=close_timeout,
            on_close=on_close,
            last_activity=now,
        )
        self._sessions[session.thread_id] = session
        return session

    async def receive(self, thread_id: str, player_id: str, content: str, *, now: datetime) -> None:
        session = self._sessions.get(thread_id)
        if session is None or session.state == "closed":
            return
        session.transcript.append(ThreadMessage(player_id=player_id, content=content, at=now))
        session.last_activity = now
        session.state = "open"
        session.checked_at = None

    async def check_idle(self, *, now: datetime) -> None:
        for session in list(self._sessions.values()):
            if session.state == "closed":
                continue

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
        )
        result = session.on_close(transcript)
        if result is not None:
            await result
