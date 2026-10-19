# core/address.py
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from services.common.memory import clean_nickname

logger = logging.getLogger(__name__)

HONORIFIC = "상"

NicknameSource = Callable[[str], Awaitable[str | None]]


class _NicknameStore(Protocol):
    async def get_nickname(self, player_id: str) -> str: ...


def build_address(*, memory_nickname: str, source_nicknames: list[str | None], discord_name: str | None) -> str | None:
    """유니가 부를 이름 하나를 정한다(00-core.md "호칭"). 메모리의 호칭은 그대로, 나머지(CHUNITHM-NET
    닉네임, 디스코드 서버 닉네임)는 뒤에 "상"을 붙인다. 어느 쪽도 없으면 None."""
    own = clean_nickname(memory_nickname)
    if own:
        return own
    for raw in [*source_nicknames, discord_name]:
        name = clean_nickname(raw or "")
        if name:
            return name if name.endswith(HONORIFIC) else f"{name}{HONORIFIC}"
    return None


class AddressResolver:
    """플레이어를 무엇이라 부를지 정한다. 호칭 칸은 `memory`에서, 게임 쪽 닉네임은 `sources`(순서대로)에서
    받는다 — 코어는 그 닉네임이 어느 게임 것인지 모른다."""

    def __init__(self, *, memory: _NicknameStore, sources: list[NicknameSource] | None = None) -> None:
        self._memory = memory
        self._sources = sources or []

    async def resolve(self, player_id: str, *, discord_name: str | None = None) -> str | None:
        memory_nickname = await self._memory.get_nickname(player_id)
        if clean_nickname(memory_nickname):
            return build_address(memory_nickname=memory_nickname, source_nicknames=[], discord_name=None)
        names: list[str | None] = []
        for source in self._sources:
            try:
                names.append(await source(player_id))
            except Exception:
                logger.warning("닉네임 조회 실패, 건너뛴다", exc_info=True)
        return build_address(memory_nickname="", source_nicknames=names, discord_name=discord_name)
