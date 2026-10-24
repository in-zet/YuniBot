import pytest

from core.tool_types import DEFAULT_GAME
from services.chunithm.profile import CHUNITHM
from services.game.registry import (
    GAME_PROFILES,
    UnknownGameError,
    choose_game,
    profile_for,
)


def test_default_game_is_registered() -> None:
    assert profile_for(DEFAULT_GAME) is CHUNITHM
    assert set(GAME_PROFILES) == {"chunithm", "maimai"}


def test_profile_for_unknown_game_is_a_user_facing_error() -> None:
    with pytest.raises(UnknownGameError, match="모르는 게임"):
        profile_for("nonexistent")


def test_choose_game_order_is_requested_then_preferred_then_default() -> None:
    assert choose_game(requested="chunithm", preferred=None) == "chunithm"
    assert choose_game(requested=None, preferred="chunithm") == "chunithm"
    assert choose_game(requested=None, preferred=None) == DEFAULT_GAME
    assert choose_game(requested="", preferred=None) == DEFAULT_GAME


def test_choose_game_ignores_an_unregistered_preferred_game_but_rejects_an_unregistered_requested_one() -> None:
    assert choose_game(requested=None, preferred="nonexistent") == DEFAULT_GAME
    with pytest.raises(UnknownGameError):
        choose_game(requested="nonexistent", preferred="chunithm")


def test_chunithm_profile_covers_every_chart_code_in_the_long_name_table() -> None:
    assert set(CHUNITHM.long_to_short.values()) == set(CHUNITHM.chart_codes)
    assert set(CHUNITHM.range_codes) <= set(CHUNITHM.chart_codes)
    assert CHUNITHM.long_to_short[CHUNITHM.default_difficulty_long] == CHUNITHM.default_difficulty_code


def test_manifest_valid_games_match_the_registry() -> None:
    from core.manifest import VALID_GAMES

    assert VALID_GAMES == set(GAME_PROFILES)
