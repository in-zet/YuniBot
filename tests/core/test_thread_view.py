# tests/core/test_thread_view.py
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from core.fallback_messages import ENDED_MESSAGES
from core.thread_view import ThreadEndView
from core.threads import ThreadManager, ThreadTranscript


class _Players:
    async def player_for_discord(self, discord_id: str) -> Any:
        return SimpleNamespace(id={"111": "p1", "222": "p2"}[discord_id]) if discord_id in {"111", "222"} else None


class _Transport:
    async def send_message(self, text: str) -> None:
        pass


class _Response:
    def __init__(self) -> None:
        self.edited: list[Any] = []

    async def edit_message(self, *, view: Any) -> None:
        self.edited.append(view)


class _Followup:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


def _interaction(user_id: int) -> Any:
    return SimpleNamespace(user=SimpleNamespace(id=user_id), response=_Response(), followup=_Followup())


async def _view(closed: list[ThreadTranscript]) -> tuple[ThreadEndView, ThreadManager, str]:
    manager = ThreadManager()
    session = await manager.open(
        skill_name="chunithm-session", player_id="p1", transport=_Transport(), channel_id="dm-1",
        on_close=closed.append, now=datetime.now(UTC),
    )
    return ThreadEndView(threads=manager, players=cast("Any", _Players()), session=session), manager, session.thread_id


async def test_only_the_conversations_player_may_press_the_button() -> None:
    view, _manager, _thread_id = await _view([])

    assert await view.interaction_check(_interaction(111)) is True
    assert await view.interaction_check(_interaction(222)) is False
    assert await view.interaction_check(_interaction(999)) is False


async def test_pressing_the_button_closes_the_conversation_at_once() -> None:
    closed: list[ThreadTranscript] = []
    view, manager, _thread_id = await _view(closed)
    interaction = _interaction(111)

    await view.end_button.callback(interaction)  # pyright: ignore[reportCallIssue]

    assert [t.closed_reason for t in closed] == ["explicit"]
    assert manager.session_for_channel("dm-1") is None
    assert interaction.response.edited == [None]  # 버튼을 지운다
    assert len(interaction.followup.sent) == 1 and interaction.followup.sent[0] in ENDED_MESSAGES
