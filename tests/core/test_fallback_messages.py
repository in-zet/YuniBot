# tests/core/test_fallback_messages.py
from core.fallback_messages import (
    COOLDOWN_ACTIVE_MESSAGE,
    LLM_FAILURE_MESSAGE,
    MONTHLY_CAP_REACHED_MESSAGE,
    TOOL_CALL_CAP_MESSAGE,
)


def test_fallback_messages_mention_slash_command_path() -> None:
    for message in (LLM_FAILURE_MESSAGE, MONTHLY_CAP_REACHED_MESSAGE):
        assert "/" in message  # 슬래시 명령은 계속 쓸 수 있다는 안내가 있어야 한다


def test_fallback_messages_are_nonempty_and_distinct() -> None:
    messages = {LLM_FAILURE_MESSAGE, MONTHLY_CAP_REACHED_MESSAGE, COOLDOWN_ACTIVE_MESSAGE, TOOL_CALL_CAP_MESSAGE}
    assert len(messages) == 4
    assert all(messages)
