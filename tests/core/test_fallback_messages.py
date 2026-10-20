# tests/core/test_fallback_messages.py
import pytest

from core import fallback_messages
from core.fallback_messages import (
    COOLDOWN_ACTIVE_MESSAGES,
    LLM_FAILURE_MESSAGES,
    MONTHLY_CAP_REACHED_MESSAGES,
    TOOL_CALL_CAP_MESSAGES,
    Messages,
    pick,
)

_ALL: dict[str, Messages] = {
    name: value for name, value in vars(fallback_messages).items() if name.endswith("_MESSAGES")
}


def test_fallback_messages_mention_slash_command_path() -> None:
    for message in (*LLM_FAILURE_MESSAGES, *MONTHLY_CAP_REACHED_MESSAGES):
        assert "/" in message  # 슬래시 명령은 계속 쓸 수 있다는 안내가 있어야 한다


@pytest.mark.parametrize("name", sorted(_ALL))
def test_every_situation_has_five_distinct_nonempty_variants(name: str) -> None:
    variants = _ALL[name]
    assert len(variants) == 5 and len(set(variants)) == 5
    assert all(v.strip() for v in variants)


def test_situations_do_not_share_variants() -> None:
    groups = (LLM_FAILURE_MESSAGES, MONTHLY_CAP_REACHED_MESSAGES, COOLDOWN_ACTIVE_MESSAGES, TOOL_CALL_CAP_MESSAGES)
    assert len({v for group in groups for v in group}) == sum(len(group) for group in groups)


def test_pick_returns_one_of_the_variants() -> None:
    assert all(pick(COOLDOWN_ACTIVE_MESSAGES) in COOLDOWN_ACTIVE_MESSAGES for _ in range(20))
