# services/game/registry.py
"""게임 등록부. 어떤 게임이 있는지 아는 곳은 여기 한 곳이다.

게임 고르기는 한 함수(`choose_game`)이고 도구 실행기와 슬래시 경로가 같은 것을 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping

from core.tool_types import DEFAULT_GAME, UserFacingError
from services.chunithm.profile import CHUNITHM
from services.game.profile import GameProfile
from services.maimai.profile import MAIMAI

GAME_PROFILES: Mapping[str, GameProfile] = {CHUNITHM.game: CHUNITHM, MAIMAI.game: MAIMAI}


class UnknownGameError(UserFacingError):
    def __init__(self, game: str) -> None:
        super().__init__(f"모르는 게임이야: {game}")
        self.game = game


def profile_for(game: str) -> GameProfile:
    try:
        return GAME_PROFILES[game]
    except KeyError:
        raise UnknownGameError(game) from None


def choose_game(*, requested: str | None, preferred: str | None) -> str:
    """어느 게임인지 정한다: 사용자가 말한 게임 → 플레이어의 주 게임 → 기본 게임.

    말한 게임이 등록돼 있지 않으면 `UnknownGameError`다. 주 게임이 등록돼 있지 않으면(게임이 빠진 경우)
    기본 게임으로 넘어간다.
    """
    if requested:
        return profile_for(requested).game
    if preferred and preferred in GAME_PROFILES:
        return preferred
    return DEFAULT_GAME
